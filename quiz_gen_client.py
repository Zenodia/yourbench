import argparse
import asyncio
import os
import shutil
from typing import Dict, List, Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.tools import Tool
from loguru import logger


def parse_metrics(result_string: str) -> Dict[str, Any]:
    """
    Parse pipe-delimited metrics string from server

    Example input:
    status:completed|total:3|successful:3|failed:0|duration:68.3s|pdf:File1|quiz_csv:path1|...

    Returns:
        Dictionary with metrics and list of processed PDFs
    """
    metrics = {
        'status': None,
        'total': 0,
        'complete': 0,
        'partial': 0,
        'failed': 0,
        'duration': None,
        'pdfs': []
    }

    parts = result_string.split('|')
    current_pdf = {}

    for part in parts:
        if ':' not in part:
            continue

        key, value = part.split(':', 1)

        # Parse overall metrics
        if key == 'status':
            metrics['status'] = value
        elif key == 'total':
            metrics['total'] = int(value)
        elif key == 'complete':
            metrics['complete'] = int(value)
        elif key == 'partial':
            metrics['partial'] = int(value)
        elif key == 'failed':
            metrics['failed'] = int(value)
        elif key == 'duration':
            metrics['duration'] = value

        # Parse per-PDF information
        elif key == 'pdf':
            # Save previous PDF if exists
            if current_pdf:
                metrics['pdfs'].append(current_pdf)
            # Start new PDF
            current_pdf = {'name': value}
        elif key == 'quiz_csv':
            current_pdf['quiz_csv'] = value
        elif key == 'summary_csv':
            current_pdf['summary_csv'] = value

    # Don't forget the last PDF
    if current_pdf:
        metrics['pdfs'].append(current_pdf)

    return metrics


def display_metrics(metrics: Dict[str, Any]):
    """
    Display parsed metrics in a user-friendly format
    """
    print("📊 PROCESSING METRICS")
    print("="*70)

    # Overall summary
    status_emoji = "✅" if metrics['status'] == 'completed' else "❌"
    print(f"\n{status_emoji} Status: {metrics['status']}")
    print(f"   Total PDFs:       {metrics['total']}")
    print(f"   ✅ Complete:      {metrics['complete']} (quiz + summary)")
    if metrics['partial'] > 0:
        print(f"   ⚠️  Partial:       {metrics['partial']} (summary only)")
    print(f"   ❌ Failed:        {metrics['failed']}")
    print(f"   ⏱️  Duration:      {metrics['duration']}")

    # Success rate (complete + partial = successful processing)
    if metrics['total'] > 0:
        processed = metrics['complete'] + metrics['partial']
        success_rate = (processed / metrics['total']) * 100
        print(f"   📈 Processed:     {success_rate:.1f}%")
        if metrics['complete'] > 0:
            complete_rate = (metrics['complete'] / metrics['total']) * 100
            print(f"   🎯 Complete Rate: {complete_rate:.1f}%")

    # Per-PDF details
    if metrics['pdfs']:
        print(f"\n📄 PROCESSED FILES ({len(metrics['pdfs'])})")
        print("-"*70)

        for i, pdf in enumerate(metrics['pdfs'], 1):
            print(f"\n{i}. {pdf['name']}")
            if 'quiz_csv' in pdf:
                print(f"   ✓ Quiz CSV:    {pdf['quiz_csv']}")
            if 'summary_csv' in pdf:
                print(f"   ✓ Summary CSV: {pdf['summary_csv']}")

    print("\n" + "="*70)


async def quiz_generation_client(
    pdf_file_dir: str = "/workspace/test_upload/",
    save_csv_dir: str = '/workspace/mnt/',
    cleanup: bool = False
) -> str:
    """Run the quiz generation pipeline and return its textual output.

    Keeps the client connected for the duration of the call using the async context manager.

    Args:
        pdf_file_dir: Directory containing PDF files
        save_csv_dir: Directory to save CSV outputs
        cleanup: Whether to clean up temporary directories

    Returns:
        Textual result from server
    """
    server_url = "http://localhost:4777/mcp"
    client = Client(transport=StreamableHttpTransport(server_url))

    try:
        async with client:
            # Validate server connection with timeout
            try:
                tools: list[Tool] = await asyncio.wait_for(client.list_tools(), timeout=5.0)
                for tool in tools:
                    print(f"Tool: {tool}")
            except asyncio.TimeoutError:
                raise ConnectionError(f"Server not responding at {server_url}")
            except (AttributeError, TypeError) as e:
                logger.warning(f"Could not list tools: {e}")
                # Non-fatal: continue to call the pipeline

            # Call the quiz generation pipeline while the client is still connected
            call_payload = {"pdf_file_dir": pdf_file_dir, "save_csv_dir": save_csv_dir}
            result = await client.call_tool("quiz_generating_pipeline", call_payload)

            # Extract text from result
            try:
                text = result.content[0].text
            except (AttributeError, IndexError, TypeError) as e:
                logger.warning(f"Could not parse result content: {e}")
                text = str(result)

            print(f"Raw result: {text}")
            print("\n" + "="*70)

            # Parse metrics from pipe-delimited string
            metrics = parse_metrics(text)
            display_metrics(metrics)

            # Cleanup temporary directories if requested
            if cleanup:
                print("Starting post-cleanup operation...\n")

                processed_dir = os.path.join(save_csv_dir, "processed")
                output_dir = os.path.join(save_csv_dir, "output_dir")

                cleaned = []
                if os.path.exists(processed_dir):
                    try:
                        shutil.rmtree(processed_dir)
                        cleaned.append("processed")
                        print(f"✓ Cleaned up: {processed_dir}")
                    except OSError as e:
                        print(f"✗ Failed to clean {processed_dir}: {e}")

                if os.path.exists(output_dir):
                    try:
                        shutil.rmtree(output_dir)
                        cleaned.append("output_dir")
                        print(f"✓ Cleaned up: {output_dir}")
                    except OSError as e:
                        print(f"✗ Failed to clean {output_dir}: {e}")

                if cleaned:
                    print(f"\nCleanup complete: removed {', '.join(cleaned)}")
                else:
                    print("No directories to clean up")

            # Return the textual result so callers can pipe it into downstream tasks
            return text

    except ConnectionError as e:
        print(f"❌ Connection Error: {e}")
        raise
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        raise



if __name__ == "__main__":
    argparser = argparse.ArgumentParser(
        description="Quiz Generation Client",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    argparser.add_argument(
        "--pdf_file_dir",
        type=str,
        default="/workspace/test_upload/",
        help="The directory containing PDF files for quiz generation.",
    )
    argparser.add_argument(
        "--save_csv_dir",
        type=str,
        default="/workspace/mnt/",
        help="The directory to save generated CSV files.",
    )
    argparser.add_argument(
        "--clean",
        action='store_true',
        default=False,
        help="Clean up temporary dirs (output_dir, processed) but keep the csv dir"
    )

    args = argparser.parse_args()

    asyncio.run(quiz_generation_client(
        pdf_file_dir=args.pdf_file_dir,
        save_csv_dir=args.save_csv_dir,
        cleanup=args.clean
    ))
