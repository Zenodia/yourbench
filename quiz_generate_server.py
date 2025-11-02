import glob
import os
import subprocess
import sys
import tempfile
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Any

import psutil
from colorama import Fore
from datasets import load_from_disk
from dotenv import load_dotenv
from fastmcp import FastMCP
from loguru import logger

import shutil as shutil_module
from yourbench.utils.loading_engine import load_config

load_dotenv()

# ============================================================================
# SERVER CONFIGURATION (from environment variables)
# ============================================================================
MAX_WORKERS_LIMIT = int(os.getenv("QUIZ_SERVER_MAX_WORKERS", "8"))
MEMORY_PER_WORKER_GB = float(os.getenv("QUIZ_SERVER_MEMORY_PER_WORKER", "2.0"))
BATCH_SIZE = int(os.getenv("QUIZ_SERVER_BATCH_SIZE", "15"))
AUTO_TUNE_WORKERS = os.getenv("QUIZ_SERVER_AUTO_TUNE", "true").lower() == "true"
BUFFER_SIZE = int(os.getenv("QUIZ_SERVER_BUFFER_SIZE", "8192"))

# Constants
LARGE_BATCH_THRESHOLD = 20
MEMORY_WARNING_THRESHOLD = 85
MEMORY_CRITICAL_THRESHOLD = 90
MEMORY_ERROR_THRESHOLD = 95

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def discover_pdfs(pdf_dir: str) -> List[str]:
    """
    Discover all PDF files in the given directory

    Args:
        pdf_dir: Directory path containing PDF files

    Returns:
        List of PDF file paths
    """
    pdf_pattern = os.path.join(pdf_dir, "*.pdf")
    pdf_files = glob.glob(pdf_pattern)
    logger.info(f"📚 Discovered {len(pdf_files)} PDF file(s) in {pdf_dir}")
    for pdf in pdf_files:
        logger.info(f"   - {os.path.basename(pdf)}")
    return sorted(pdf_files)


def check_for_api_errors(log_file_path: str) -> str:
    """
    Check log file for common API errors

    Args:
        log_file_path: Path to subprocess log file

    Returns:
        Error description if API error found, empty string otherwise
    """
    if not os.path.exists(log_file_path):
        return ""

    try:
        with open(log_file_path, 'r') as f:
            log_content = f.read().lower()

        # Check for common API error patterns
        api_error_patterns = [
            ("rate limit", "Rate limit exceeded"),
            ("quota exceeded", "API quota exceeded"),
            ("401", "Authentication failed"),
            ("403", "Authorization failed"),
            ("timeout", "API timeout"),
            ("connection refused", "Cannot connect to API"),
            ("api key", "API key issue"),
            ("openai", "OpenAI API error"),
            ("anthropic", "Anthropic API error"),
        ]

        for pattern, description in api_error_patterns:
            if pattern in log_content:
                return description

        return ""
    except Exception:
        return ""


def calculate_optimal_workers(num_pdfs: int) -> int:
    """
    Calculate optimal number of workers based on system resources

    Args:
        num_pdfs: Total number of PDFs to process

    Returns:
        Optimal number of workers
    """
    # Get available system resources
    cpu_count = os.cpu_count() or 1
    available_memory_gb = psutil.virtual_memory().available / (1024**3)

    # Calculate limits
    cpu_workers = max(1, cpu_count - 1)  # Leave 1 core for system
    memory_workers = max(1, int(available_memory_gb / MEMORY_PER_WORKER_GB))
    user_limit = MAX_WORKERS_LIMIT

    # Take the minimum
    optimal = min(cpu_workers, memory_workers, user_limit)

    # For large batches, be conservative
    if num_pdfs > LARGE_BATCH_THRESHOLD:
        optimal = min(optimal, 3)

    # For single PDF, use 1 worker
    if num_pdfs == 1:
        optimal = 1

    logger.info(f"🔧 Worker calculation:")
    logger.info(f"   CPUs available: {cpu_count} (using {cpu_workers})")
    logger.info(f"   Memory available: {available_memory_gb:.1f}GB (allows {memory_workers} workers)")
    logger.info(f"   User limit: {user_limit}")
    logger.info(f"   Optimal workers: {optimal}")

    return optimal


def process_single_pdf(pdf_path: str, base_save_dir: str, config_template_path: str) -> Dict[str, Any]:
    """
    Process a single PDF file through the yourbench pipeline

    Args:
        pdf_path: Path to the PDF file
        base_save_dir: Base directory for saving outputs
        config_template_path: Path to the YAML config template

    Returns:
        Dictionary with processing results
    """
    pdf_name = Path(pdf_path).stem
    start_time = time.time()

    logger.info(f"🔄 Processing: {pdf_name}")

    try:
        # Create isolated working directory for this PDF
        pdf_work_dir = os.path.join(base_save_dir, pdf_name)
        os.makedirs(os.path.join(pdf_work_dir, "output_dir"), exist_ok=True)
        os.makedirs(os.path.join(pdf_work_dir, "processed"), exist_ok=True)
        os.makedirs(os.path.join(pdf_work_dir, "csv"), exist_ok=True)

        # Create PDF-specific directory to hold just this PDF
        pdf_input_dir = os.path.join(pdf_work_dir, "input")
        os.makedirs(pdf_input_dir, exist_ok=True)

        # Copy PDF to isolated input directory
        pdf_input_path = os.path.join(pdf_input_dir, os.path.basename(pdf_path))
        if not os.path.exists(pdf_input_path):
            shutil_module.copy2(pdf_path, pdf_input_path)

        # Load and customize config for this PDF
        config = load_config(config_template_path)
        config["pipeline"]["ingestion"]["source_documents_dir"] = pdf_input_dir
        config["hf_configuration"]["local_dataset_dir"] = os.path.join(pdf_work_dir, "output_dir")
        config["pipeline"]["ingestion"]["output_dir"] = os.path.join(pdf_work_dir, "processed")
        config["pipeline"]["upload_ingest_to_hub"]["source_documents_dir"] = os.path.join(pdf_work_dir, "processed")

        # Write PDF-specific config
        test_yaml_path = os.path.join(pdf_work_dir, f"config_{pdf_name}.yaml")
        with open(test_yaml_path, "w") as yaml_f:
            yaml.dump(config, yaml_f)

        # Run yourbench subprocess
        subprocess_start = time.time()
        yourbench_cmd = os.path.join(os.path.dirname(sys.executable), "yourbench")

        # Validate yourbench executable exists
        if not os.path.isfile(yourbench_cmd):
            raise FileNotFoundError(f"yourbench executable not found: {yourbench_cmd}")

        log_file_path = os.path.join(pdf_work_dir, "logfile.txt")

        # Use context manager for subprocess
        with subprocess.Popen(
            [yourbench_cmd, "run", f"--config={test_yaml_path}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        ) as proc:
            # Read output with buffered I/O
            with open(log_file_path, 'wb') as logfile:
                while True:
                    chunk = proc.stdout.read(BUFFER_SIZE)
                    if not chunk:
                        break
                    logfile.write(chunk)

            returncode = proc.wait()
            subprocess_duration = time.time() - subprocess_start

            if returncode != 0:
                # Include tail of log in error message
                try:
                    with open(log_file_path, 'r') as f:
                        log_lines = f.readlines()
                        last_lines = ''.join(log_lines[-20:]) if log_lines else 'No log output'
                except Exception:
                    last_lines = 'Could not read log file'

                raise subprocess.CalledProcessError(
                    returncode, yourbench_cmd,
                    output=f"Process failed with return code {returncode}\nLast log lines:\n{last_lines}"
                )

        # Load and save datasets
        load_dir = config["hf_configuration"]["local_dataset_dir"]

        # Load datasets
        chunked_dataset = load_from_disk(dataset_path=os.path.join(load_dir, "chunked"))
        summarized_dataset = load_from_disk(dataset_path=os.path.join(load_dir, "summarized"))

        # Save CSVs
        save_csv_path = os.path.join(pdf_work_dir, 'csv')
        single_shot_file = None

        # Try to load and save quiz questions
        single_shot_path = os.path.join(load_dir, "single_shot_questions")
        if not os.path.exists(single_shot_path):
            # Check log for API errors
            api_error = check_for_api_errors(log_file_path)
            if api_error:
                logger.warning(f"⚠️  No quiz questions for {pdf_name}: LLM API issue - {api_error}")
            else:
                logger.warning(f"⚠️  No quiz questions for {pdf_name}: Pipeline didn't generate questions (check: content quality, sampling, config)")
        else:
            try:
                single_shot_questions = load_from_disk(dataset_path=single_shot_path)
                single_shot_qs = single_shot_questions.to_pandas()
                single_shot_file = os.path.join(save_csv_path, f"{pdf_name}.csv")
                single_shot_qs.to_csv(single_shot_file, index=False)
            except Exception as e:
                logger.error(f"❌ Failed to save quiz questions for {pdf_name}: {e}")

        summary_file = os.path.join(save_csv_path, f"summary_{pdf_name}.csv")
        summarized_dataset.to_csv(summary_file, index=False)

        total_duration = time.time() - start_time

        # Determine completion status
        if single_shot_file:
            completion_status = "complete"
            logger.info(f"✅ Completed: {pdf_name} in {total_duration:.1f}s")
        else:
            completion_status = "partial"
            logger.warning(f"⚠️  Completed with warnings: {pdf_name} in {total_duration:.1f}s (no quiz questions)")

        return {
            "status": "success",
            "completion": completion_status,  # "complete" or "partial"
            "pdf_name": pdf_name,
            "pdf_path": pdf_path,
            "work_dir": pdf_work_dir,
            "single_shot_csv": single_shot_file,
            "summary_csv": summary_file,
            "duration": total_duration,
            "subprocess_duration": subprocess_duration,
            "has_quiz_questions": single_shot_file is not None
        }

    except (subprocess.SubprocessError, OSError, IOError, FileNotFoundError) as e:
        total_duration = time.time() - start_time
        logger.error(f"❌ Failed: {pdf_name} - {str(e)}")
        return {
            "status": "failed",
            "pdf_name": pdf_name,
            "pdf_path": pdf_path,
            "error": str(e),
            "duration": total_duration
        }
    except Exception as e:
        # Unexpected errors - log with full traceback
        total_duration = time.time() - start_time
        logger.exception(f"❌ Unexpected error in {pdf_name}")
        return {
            "status": "failed",
            "pdf_name": pdf_name,
            "pdf_path": pdf_path,
            "error": f"Unexpected error: {str(e)}",
            "duration": total_duration
        }


def merge_results_to_original_format(results: List[Dict[str, Any]], save_csv_dir: str) -> Dict[str, str]:
    """
    Merge individual PDF results into original combined output format

    Args:
        results: List of processing results from individual PDFs
        save_csv_dir: Base save directory (used for naming)

    Returns:
        Dictionary with paths to merged output files
    """
    import pandas as pd

    logger.info("📦 Merging results into original output format...")

    # Create output directories (original format)
    os.makedirs(os.path.join(save_csv_dir, "csv"), exist_ok=True)

    # Use save_csv_dir name for output files (original behavior)
    output_name = Path(save_csv_dir).name.strip() or "output"

    # Collect all successful results
    successful = [r for r in results if r["status"] == "success"]

    if not successful:
        logger.warning("No successful results to merge")
        return {"status": "no_results"}

    # Merge quiz CSVs
    quiz_dfs = []
    for result in successful:
        if result.get('single_shot_csv') and os.path.exists(result['single_shot_csv']):
            try:
                df = pd.read_csv(result['single_shot_csv'])
                # Add source PDF column for traceability
                df['source_pdf'] = result['pdf_name']
                quiz_dfs.append(df)
            except Exception as e:
                logger.warning(f"Could not read {result['single_shot_csv']}: {e}")

    # Merge summary CSVs
    summary_dfs = []
    for result in successful:
        if result.get('summary_csv') and os.path.exists(result['summary_csv']):
            try:
                df = pd.read_csv(result['summary_csv'])
                # Add source PDF column for traceability
                df['source_pdf'] = result['pdf_name']
                summary_dfs.append(df)
            except Exception as e:
                logger.warning(f"Could not read {result['summary_csv']}: {e}")

    # Save merged files (original format)
    merged_files = {}

    if quiz_dfs:
        merged_quiz = pd.concat(quiz_dfs, ignore_index=True)
        quiz_output = os.path.join(save_csv_dir, "csv", f"{output_name}.csv")
        merged_quiz.to_csv(quiz_output, index=False)
        merged_files['quiz_csv'] = quiz_output
        logger.info(f"✅ Merged quiz CSV: {quiz_output} ({len(merged_quiz)} rows)")

    if summary_dfs:
        merged_summary = pd.concat(summary_dfs, ignore_index=True)
        summary_output = os.path.join(save_csv_dir, "csv", f"summary_{output_name}.csv")
        merged_summary.to_csv(summary_output, index=False)
        merged_files['summary_csv'] = summary_output
        logger.info(f"✅ Merged summary CSV: {summary_output} ({len(merged_summary)} rows)")

    return merged_files


def validate_directories(pdf_dir: str, save_dir: str):
    """
    Validate input and output directories

    Args:
        pdf_dir: PDF input directory
        save_dir: CSV output directory

    Raises:
        ValueError: If directories don't exist or aren't accessible
        PermissionError: If directories aren't readable/writable
    """
    if not os.path.isdir(pdf_dir):
        raise ValueError(f"PDF directory not found: {pdf_dir}")
    if not os.access(pdf_dir, os.R_OK):
        raise PermissionError(f"Cannot read PDF directory: {pdf_dir}")

    # Create save directory if it doesn't exist
    os.makedirs(save_dir, exist_ok=True)
    if not os.access(save_dir, os.W_OK):
        raise PermissionError(f"Cannot write to save directory: {save_dir}")


mcp = FastMCP("MCPTools")
@mcp.tool()
def quiz_generating_pipeline(pdf_file_dir: str, save_csv_dir: str) -> str:
    """
    Generate quiz questions from PDF files with automatic parallel processing

    Server automatically detects all PDFs in the directory and processes them
    in parallel based on available system resources.

    Args:
        pdf_file_dir: Directory containing PDF file(s)
        save_csv_dir: Directory to save output CSV files

    Returns:
        Summary message of processed PDFs
    """
    start_time = time.time()
    logger.info(f"🚀 Starting quiz generation pipeline at {time.strftime('%H:%M:%S')}")
    logger.info(f"   MAX_WORKERS_LIMIT: {MAX_WORKERS_LIMIT}")
    logger.info(f"   MEMORY_PER_WORKER_GB: {MEMORY_PER_WORKER_GB}")
    logger.info(f"   BATCH_SIZE: {BATCH_SIZE}")
    logger.info(f"   AUTO_TUNE_WORKERS: {AUTO_TUNE_WORKERS}")
    print(Fore.BLUE + f"pdf_file_dir = {pdf_file_dir}\nsave_csv_dir = {save_csv_dir}" + Fore.RESET)

    # Validate directories
    try:
        validate_directories(pdf_file_dir, save_csv_dir)
    except (ValueError, PermissionError) as e:
        logger.error(f"❌ Directory validation failed: {e}")
        return f"status:error|message:Directory validation failed: {e}"

    # Discover all PDFs
    pdf_files = discover_pdfs(pdf_file_dir)
    total_pdfs = len(pdf_files)

    if total_pdfs == 0:
        logger.warning("⚠️  No PDF files found!")
        return "status:no_pdfs_found|message:No PDF files found in directory"

    # Calculate optimal workers
    max_workers = calculate_optimal_workers(total_pdfs)

    # Get config template path with validation
    config_template_path = os.getenv("QUIZ_CONFIG_TEMPLATE", "./my_example.yaml")
    if not os.path.exists(config_template_path):
        logger.error(f"❌ Config template not found: {config_template_path}")
        return f"status:error|message:Config template not found: {config_template_path}"

    # Create temporary directory for parallel processing
    temp_base_dir = tempfile.mkdtemp(prefix="quiz_gen_")
    logger.info(f"📁 Using temp directory: {temp_base_dir}")

    # Process PDFs
    results = []

    try:
        if total_pdfs == 1:
            # Single PDF - process directly (no parallelization overhead)
            logger.info("📝 Processing single PDF (sequential mode)")
            result = process_single_pdf(pdf_files[0], temp_base_dir, config_template_path)
            results.append(result)

        else:
            # Multiple PDFs - parallel processing
            logger.info(f"🚀 Processing {total_pdfs} PDFs in parallel with {max_workers} workers")

            # Use threading for I/O-bound parallel execution (simpler than ProcessPoolExecutor)
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all jobs
                future_to_pdf = {
                    executor.submit(process_single_pdf, pdf_path, temp_base_dir, config_template_path): pdf_path
                    for pdf_path in pdf_files
                }

                # Collect results as they complete
                completed = 0
                for future in as_completed(future_to_pdf):
                    pdf_path = future_to_pdf[future]
                    try:
                        result = future.result()
                        results.append(result)
                        completed += 1

                        # Progress update
                        progress_pct = (completed / total_pdfs) * 100
                        logger.info(f"📊 Progress: {completed}/{total_pdfs} ({progress_pct:.1f}%)")

                        # Memory check with throttling
                        mem_percent = psutil.virtual_memory().percent
                        if mem_percent > MEMORY_ERROR_THRESHOLD:
                            logger.error(f"🛑 Critical memory: {mem_percent:.1f}% - stopping")
                            raise MemoryError(f"Out of memory: {mem_percent:.1f}%")
                        elif mem_percent > MEMORY_CRITICAL_THRESHOLD:
                            logger.warning(f"⚠️  Critical memory: {mem_percent:.1f}% - pausing")
                            time.sleep(5)  # Give system time to recover
                        elif mem_percent > MEMORY_WARNING_THRESHOLD:
                            logger.warning(f"⚠️  High memory usage: {mem_percent:.1f}%")

                    except (subprocess.SubprocessError, OSError, IOError, MemoryError) as e:
                        logger.error(f"❌ Exception processing {os.path.basename(pdf_path)}: {e}")
                        results.append({
                            "status": "failed",
                            "pdf_path": pdf_path,
                            "pdf_name": Path(pdf_path).stem,
                            "error": str(e)
                        })
                        # Re-raise MemoryError to stop further processing
                        if isinstance(e, MemoryError):
                            raise

        # Merge results into original output format
        merged_files = merge_results_to_original_format(results, save_csv_dir)

    finally:
        # Cleanup temp directory
        try:
            shutil_module.rmtree(temp_base_dir)
            logger.info(f"🧹 Cleaned up temp directory: {temp_base_dir}")
        except Exception as e:
            logger.warning(f"Could not cleanup temp directory: {e}")

    # Aggregate results
    total_duration = time.time() - start_time
    successful = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "failed"]

    # Break down successful into complete vs partial
    complete = [r for r in successful if r.get("completion") == "complete"]
    partial = [r for r in successful if r.get("completion") == "partial"]

    logger.info("=" * 70)
    logger.info(f"🎉 Pipeline completed in {total_duration:.1f}s ({total_duration/60:.1f} minutes)")
    logger.info(f"   Total PDFs: {total_pdfs}")
    logger.info(f"   ✅ Complete: {len(complete)} (quiz questions + summaries)")
    if partial:
        logger.info(f"   ⚠️  Partial: {len(partial)} (summaries only, no quiz questions)")
    logger.info(f"   ❌ Failed: {len(failed)}")

    if successful:
        logger.info(f"   Average time per PDF: {sum(r['duration'] for r in successful)/len(successful):.1f}s")

    # Print summary
    if complete:
        print(Fore.GREEN + "\n✅ Complete (quiz + summary):" + Fore.RESET)
        for r in complete:
            print(f"   - {r['pdf_name']}: {r['duration']:.1f}s")

    if partial:
        print(Fore.YELLOW + "\n⚠️  Partial (summary only):" + Fore.RESET)
        for r in partial:
            print(f"   - {r['pdf_name']}: {r['duration']:.1f}s")

    if failed:
        print(Fore.RED + "\n❌ Failed:" + Fore.RESET)
        for r in failed:
            print(f"   - {r['pdf_name']}: {r.get('error', 'Unknown error')}")

    # Show merged output location (original format)
    if merged_files:
        print(Fore.CYAN + "\n📁 Output files (original format):" + Fore.RESET)
        if 'quiz_csv' in merged_files:
            print(f"   Quiz CSV:    {merged_files['quiz_csv']}")
        if 'summary_csv' in merged_files:
            print(f"   Summary CSV: {merged_files['summary_csv']}")

    logger.info("=" * 70)

    # Format output message (original format - single combined file)
    output_name = Path(save_csv_dir).name.strip() or "output"
    output_parts = [
        f"status:completed",
        f"total:{total_pdfs}",
        f"complete:{len(complete)}",
        f"partial:{len(partial)}",
        f"failed:{len(failed)}",
        f"duration:{total_duration:.1f}s",
        f"pdf_file:{output_name}.pdf"  # Original naming convention
    ]

    # Add merged file paths (original format)
    if 'quiz_csv' in merged_files:
        output_parts.append(f"single_shot_csv_file:{merged_files['quiz_csv']}")
    if 'summary_csv' in merged_files:
        output_parts.append(f"summary_file:{merged_files['summary_csv']}")

    return "|".join(output_parts)


mcp.run(transport="streamable-http",
        host="127.0.0.1",
        port=4777,
        log_level="debug",
        )
