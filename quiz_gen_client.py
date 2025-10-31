import asyncio

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.tools import Tool
import argparse
import shutil
import os
from typing import Dict, List, Any


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
        'successful': 0,
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
        elif key == 'successful':
            metrics['successful'] = int(value)
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
    print(f"   ✅ Successful:    {metrics['successful']}")
    print(f"   ❌ Failed:        {metrics['failed']}")
    print(f"   ⏱️  Duration:      {metrics['duration']}")

    # Success rate
    if metrics['total'] > 0:
        success_rate = (metrics['successful'] / metrics['total']) * 100
        print(f"   📈 Success Rate:  {success_rate:.1f}%")

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


async def quiz_generation_client(pdf_file_dir: str = "/workspace/test_upload/", save_csv_dir: str ='/workspace/mnt/', cleanup:bool =False):
    """Run the quiz generation pipeline and return its textual output.

    Keeps the client connected for the duration of the call using the async context manager.
    """
    client = Client(transport=StreamableHttpTransport("http://localhost:4777/mcp"))
    async with client:
        # list available tools (debugging) -- optional
        try:
            tools: list[Tool] = await client.list_tools()
            for tool in tools:
                print(f"Tool: {tool}")
        except Exception:
            # non-fatal: continue to call the pipeline even if listing fails
            pass

        # call the quiz generation pipeline while the client is still connected
        call_payload = {"pdf_file_dir": pdf_file_dir, "save_csv_dir": save_csv_dir}
        result = await client.call_tool("quiz_generating_pipeline", call_payload)
        # result may be an object with .content; guard access
        try:
            text = result.content[0].text
        except Exception:
            # fallback to str(result)
            text = str(result)

        print(f"Raw result: {text}")
        print("\n" + "="*70)

        # Parse metrics from pipe-delimited string
        metrics = parse_metrics(text)
        display_metrics(metrics)
        if cleanup:
            print("starting post clean up operation .... \n")
            os.path.join(save_csv_dir, "processed")
            if os.path.exists(os.path.join(save_csv_dir,'/output_dir')):
                shutil.rmtree(os.path.join(save_csv_dir, "processed"), ignore_errors=False, onerror=None)
                print("clean up dir : /workspace/mnt/output_dir successfully!\n")
            if os.path.exists(os.path.join(save_csv_dir, "output_dir")):
                shutil.rmtree(os.path.join(save_csv_dir, "output_dir"), ignore_errors=False, onerror=None)
                print("clean up dir : /workspace/mnt/processed successfully \n exiting client !")

        # return the textual result so callers can pipe it into downstream tasks
        return text
        #result = await client.call_tool("tavily_concurrent_search_async", {"search_queries": ["Who is Leonardo Da Vinci?","what is the difference between CPU and GPU?"], "tavily_topic":"general","tavily_days":1})
        #print(type(result))
        #print(f" ---- \n result: \n\n {result} ----")



if __name__ == "__main__":
    argparser = argparse.ArgumentParser(description="Quiz Generation Client")
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
    argparser.add_argument("--clean", type=bool, default=True, help="clean up temporary dirs such as /workspace/mnt/output_dir, /workspace/mnt/processed but kept the csv dir")
    args = argparser.parse_args()
    pdf_file_dir=args.pdf_file_dir
    save_csv_dir=args.save_csv_dir
    cleanup=args.clean
    asyncio.run(quiz_generation_client(pdf_file_dir=pdf_file_dir, save_csv_dir=save_csv_dir, cleanup=cleanup))
