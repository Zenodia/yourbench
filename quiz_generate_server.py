from fastmcp import FastMCP
from dotenv import load_dotenv
from colorama import Fore
import asyncio
import subprocess
import time
import glob
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Any
import psutil
import tempfile
import shutil as shutil_module

import sys, os
from yourbench.pipeline.single_shot_question_generation import _load_stage_config
import random
from dataclasses import field, dataclass

from loguru import logger

from datasets import Dataset
from yourbench.utils.prompts import (
    QUESTION_GENERATION_USER_PROMPT,
    QUESTION_GENERATION_SYSTEM_PROMPT,
    QUESTION_GENERATION_SYSTEM_PROMPT_MULTI,
)
from yourbench.utils.dataset_engine import (
    custom_load_dataset,
    custom_save_dataset,
)

# Import the unified parsing function
from yourbench.utils.parsing_engine import shuffle_mcq, parse_qa_pairs_from_response
from yourbench.utils.inference_engine import InferenceCall, run_inference
from yourbench.utils.loading_engine import load_config
from datasets import Dataset, DatasetDict, load_dataset, load_from_disk, concatenate_datasets
import yaml
from colorama import Fore
load_dotenv()

# ============================================================================
# SERVER CONFIGURATION (from environment variables)
# ============================================================================
MAX_WORKERS_LIMIT = int(os.getenv("QUIZ_SERVER_MAX_WORKERS", "8"))
MEMORY_PER_WORKER_GB = float(os.getenv("QUIZ_SERVER_MEMORY_PER_WORKER", "2.0"))
BATCH_SIZE = int(os.getenv("QUIZ_SERVER_BATCH_SIZE", "15"))
AUTO_TUNE_WORKERS = os.getenv("QUIZ_SERVER_AUTO_TUNE", "true").lower() == "true"
BUFFER_SIZE = int(os.getenv("QUIZ_SERVER_BUFFER_SIZE", "8192"))

logger.info("🔧 Server Configuration:")
logger.info(f"   MAX_WORKERS_LIMIT: {MAX_WORKERS_LIMIT}")
logger.info(f"   MEMORY_PER_WORKER_GB: {MEMORY_PER_WORKER_GB}")
logger.info(f"   BATCH_SIZE: {BATCH_SIZE}")
logger.info(f"   AUTO_TUNE_WORKERS: {AUTO_TUNE_WORKERS}")

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
    if num_pdfs > 20:
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

        # Copy PDF to isolated input directory (or create symlink)
        import shutil
        pdf_input_path = os.path.join(pdf_input_dir, os.path.basename(pdf_path))
        if not os.path.exists(pdf_input_path):
            shutil.copy2(pdf_path, pdf_input_path)

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
        log_file_path = os.path.join(pdf_work_dir, "logfile.txt")

        proc = subprocess.Popen(
            [yourbench_cmd, "run", f"--config={test_yaml_path}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )

        # Read output with buffered I/O
        with open(log_file_path, 'wb') as logfile:
            while True:
                chunk = proc.stdout.read(BUFFER_SIZE)
                if not chunk:
                    break
                logfile.write(chunk)

        proc.wait()
        subprocess_duration = time.time() - subprocess_start

        if proc.returncode != 0:
            raise Exception(f"yourbench process failed with return code {proc.returncode}")

        # Load and save datasets
        load_dir = config["hf_configuration"]["local_dataset_dir"]

        # Load datasets
        chunked_dataset = load_from_disk(dataset_path=os.path.join(load_dir, "chunked"))
        summarized_dataset = load_from_disk(dataset_path=os.path.join(load_dir, "summarized"))

        # Save CSVs
        save_csv_path = os.path.join(pdf_work_dir, 'csv')
        single_shot_file = None

        try:
            single_shot_questions = load_from_disk(dataset_path=os.path.join(load_dir, "single_shot_questions"))
            single_shot_qs = single_shot_questions.to_pandas()
            single_shot_file = os.path.join(save_csv_path, f"{pdf_name}.csv")
            single_shot_qs.to_csv(single_shot_file, index=False)
        except Exception as e:
            logger.warning(f"Could not save single shot questions for {pdf_name}: {e}")

        summary_file = os.path.join(save_csv_path, f"summary_{pdf_name}.csv")
        summarized_dataset.to_csv(summary_file, index=False)

        total_duration = time.time() - start_time

        logger.info(f"✅ Completed: {pdf_name} in {total_duration:.1f}s")

        return {
            "status": "success",
            "pdf_name": pdf_name,
            "pdf_path": pdf_path,
            "work_dir": pdf_work_dir,
            "single_shot_csv": single_shot_file,
            "summary_csv": summary_file,
            "duration": total_duration,
            "subprocess_duration": subprocess_duration
        }

    except Exception as e:
        total_duration = time.time() - start_time
        logger.error(f"❌ Failed: {pdf_name} - {str(e)}")
        return {
            "status": "failed",
            "pdf_name": pdf_name,
            "pdf_path": pdf_path,
            "error": str(e),
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
    output_name = Path(save_csv_dir).name if save_csv_dir.rstrip('/') else "output"

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
    print(Fore.BLUE + f"pdf_file_dir = {pdf_file_dir}\nsave_csv_dir = {save_csv_dir}" + Fore.RESET)

    # Discover all PDFs
    pdf_files = discover_pdfs(pdf_file_dir)
    total_pdfs = len(pdf_files)

    if total_pdfs == 0:
        logger.warning("⚠️  No PDF files found!")
        return "status:no_pdfs_found|message:No PDF files found in directory"

    # Calculate optimal workers
    max_workers = calculate_optimal_workers(total_pdfs)

    # Get config template path
    config_template_path = "./my_example.yaml"

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

                        # Memory check
                        mem_percent = psutil.virtual_memory().percent
                        if mem_percent > 85:
                            logger.warning(f"⚠️  High memory usage: {mem_percent:.1f}%")

                    except Exception as e:
                        logger.error(f"❌ Exception processing {os.path.basename(pdf_path)}: {e}")
                        results.append({
                            "status": "failed",
                            "pdf_path": pdf_path,
                            "pdf_name": Path(pdf_path).stem,
                            "error": str(e)
                        })

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

    logger.info("=" * 70)
    logger.info(f"🎉 Pipeline completed in {total_duration:.1f}s ({total_duration/60:.1f} minutes)")
    logger.info(f"   Total PDFs: {total_pdfs}")
    logger.info(f"   Successful: {len(successful)}")
    logger.info(f"   Failed: {len(failed)}")

    if successful:
        logger.info(f"   Average time per PDF: {sum(r['duration'] for r in successful)/len(successful):.1f}s")

    # Print summary
    print(Fore.GREEN + "\n✅ Successfully processed:" + Fore.RESET)
    for r in successful:
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
    output_name = Path(save_csv_dir).name if save_csv_dir.rstrip('/') else "output"
    output_parts = [
        f"status:completed",
        f"total:{total_pdfs}",
        f"successful:{len(successful)}",
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
