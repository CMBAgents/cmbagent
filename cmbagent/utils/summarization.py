"""
CMBAgent Summarization Utilities

This module provides document summarization capabilities for scientific papers.
It includes functions to summarize single or multiple markdown documents using
specialized AI agents.
"""

import os
import time
import json
import shutil
import glob
from pathlib import Path
from typing import Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from .utils import (
    get_api_keys_from_env,
    get_model_config,
    work_dir_default,
    default_agents_llm_model,
)
from ..cmbagent import CMBAgent


def clean_work_dir(work_dir):
    """Clean the work directory before starting new work."""
    from shutil import rmtree
    work_dir = Path(work_dir)
    if work_dir.exists():
        for item in work_dir.iterdir():
            if item.is_dir():
                rmtree(item, ignore_errors=True)
            else:
                item.unlink()


def _parse_formatted_content(content):
    """
    Parse the formatted markdown content from summarizer_response_formatter to extract structured data.
    The content is in markdown format with specific sections.
    """
    import re

    summary_data = {}

    try:
        # Extract title (first heading)
        title_match = re.search(r'^# (.+)', content, re.MULTILINE)
        if title_match:
            summary_data['title'] = title_match.group(1).strip()

        # Extract authors
        authors_match = re.search(r'\*\*Authors:\*\*\s*(.+)', content)
        if authors_match:
            authors_text = authors_match.group(1).strip()
            summary_data['authors'] = [author.strip() for author in authors_text.split(',')]

        # Extract date
        date_match = re.search(r'\*\*Date:\*\*\s*(.+)', content)
        if date_match:
            summary_data['date'] = date_match.group(1).strip()

        # Extract journal
        journal_match = re.search(r'\*\*Journal:\*\*\s*(.+)', content)
        if journal_match:
            summary_data['journal'] = journal_match.group(1).strip()

        # Extract abstract
        abstract_match = re.search(r'\*\*Abstract:\*\*\s*(.+?)(?=\n\n\*\*|\n\*\*|\Z)', content, re.DOTALL)
        if abstract_match:
            summary_data['abstract'] = abstract_match.group(1).strip()

        # Extract keywords
        keywords_match = re.search(r'\*\*Keywords:\*\*\s*(.+?)(?=\n\n\*\*|\n\*\*|\Z)', content, re.DOTALL)
        if keywords_match:
            keywords_text = keywords_match.group(1).strip()
            summary_data['keywords'] = [keyword.strip() for keyword in keywords_text.split(',')]

        # Extract key findings
        findings_match = re.search(r'\*\*Key Findings:\*\*\s*\n(.*?)(?=\n\n\*\*|\n\*\*|\Z)', content, re.DOTALL)
        if findings_match:
            findings_text = findings_match.group(1).strip()
            findings_lines = [line.strip('- ').strip() for line in findings_text.split('\n') if line.strip() and line.strip().startswith('-')]
            summary_data['key_findings'] = findings_lines

        # Extract scientific software
        software_match = re.search(r'\*\*Scientific Software:\*\*\s*\n(.*?)(?=\n\n\*\*|\n\*\*|\Z)', content, re.DOTALL)
        if software_match:
            software_text = software_match.group(1).strip()
            if software_text and not software_text.lower().startswith('none'):
                software_lines = [line.strip('- ').strip() for line in software_text.split('\n') if line.strip() and line.strip().startswith('-')]
                summary_data['scientific_software'] = software_lines
            else:
                summary_data['scientific_software'] = []

        # Extract data sources
        sources_match = re.search(r'\*\*Data Sources:\*\*\s*\n(.*?)(?=\n\n\*\*|\n\*\*|\Z)', content, re.DOTALL)
        if sources_match:
            sources_text = sources_match.group(1).strip()
            if sources_text and not sources_text.lower().startswith('none'):
                sources_lines = [line.strip('- ').strip() for line in sources_text.split('\n') if line.strip() and line.strip().startswith('-')]
                summary_data['data_sources'] = sources_lines
            else:
                summary_data['data_sources'] = []

        # Extract data sets
        datasets_match = re.search(r'\*\*Data Sets:\*\*\s*\n(.*?)(?=\n\n\*\*|\n\*\*|\Z)', content, re.DOTALL)
        if datasets_match:
            datasets_text = datasets_match.group(1).strip()
            if datasets_text and not datasets_text.lower().startswith('none'):
                datasets_lines = [line.strip('- ').strip() for line in datasets_text.split('\n') if line.strip() and line.strip().startswith('-')]
                summary_data['data_sets'] = datasets_lines
            else:
                summary_data['data_sets'] = []

        # Extract data analysis methods
        methods_match = re.search(r'\*\*Data Analysis Methods:\*\*\s*\n(.*?)(?=\n\n\*\*|\n\*\*|\Z)', content, re.DOTALL)
        if methods_match:
            methods_text = methods_match.group(1).strip()
            if methods_text:
                methods_lines = [line.strip('- ').strip() for line in methods_text.split('\n') if line.strip() and line.strip().startswith('-')]
                summary_data['data_analysis_methods'] = methods_lines
            else:
                summary_data['data_analysis_methods'] = []

    except Exception as e:
        print(f"Warning: Error parsing formatted content: {e}")
        return None

    return summary_data if summary_data else None


def summarize_document(markdown_document_path,
                       work_dir=work_dir_default,
                       clear_work_dir=True,
                       summarizer_model=default_agents_llm_model['summarizer'],
                       summarizer_response_formatter_model=default_agents_llm_model['summarizer_response_formatter']):
    """
    Summarize a single markdown document using CMBAgent summarizer agents.

    Args:
        markdown_document_path: Path to the markdown document to summarize
        work_dir: Working directory for output files
        clear_work_dir: Whether to clear the working directory before starting
        summarizer_model: Model to use for the summarizer agent
        summarizer_response_formatter_model: Model to use for the formatter agent

    Returns:
        dict: Structured document summary with title, authors, abstract, etc.
    """
    api_keys = get_api_keys_from_env()

    # Load the document from the document_path to markdown file
    with open(markdown_document_path, 'r') as f:
        markdown_document = f.read()

    # Create work directory if it doesn't exist
    work_dir = Path(work_dir).expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    if clear_work_dir:
        clean_work_dir(work_dir)

    summarizer_config = get_model_config(summarizer_model, api_keys)
    summarizer_response_formatter_config = get_model_config(summarizer_response_formatter_model, api_keys)

    cmbagent = CMBAgent(
        work_dir=work_dir,
        agent_llm_configs={
            'summarizer': summarizer_config,
            'summarizer_response_formatter': summarizer_response_formatter_config,
        },
        api_keys=api_keys,
    )

    start_time = time.time()
    cmbagent.solve(
        markdown_document,
        max_rounds=10,
        initial_agent="summarizer",
        shared_context={'current_plan_step_number': 'document_summarizer'}
    )
    end_time = time.time()
    execution_time_summarization = end_time - start_time

    # Extract the final result from the CMBAgent
    final_context = cmbagent.final_context.data if hasattr(cmbagent.final_context, 'data') else cmbagent.final_context
    chat_result = cmbagent.chat_result

    # Extract structured JSON from the chat result
    document_summary = None
    if hasattr(chat_result, 'chat_history'):
        # Look for the summarizer_response_formatter response in the chat history
        # This agent uses a Pydantic BaseModel with structured output
        for message in chat_result.chat_history:
            if isinstance(message, dict) and message.get('name') == 'summarizer_response_formatter':
                # The response_format should contain the structured data
                # Try to extract from tool_calls or parse the formatted content
                if 'tool_calls' in message:
                    # Try to get structured data from tool calls
                    for tool_call in message.get('tool_calls', []):
                        if hasattr(tool_call, 'function') and hasattr(tool_call.function, 'arguments'):
                            try:
                                document_summary = json.loads(tool_call.function.arguments)
                                break
                            except:
                                continue

                if not document_summary:
                    # Fallback: parse the formatted content back to structured data
                    formatter_content = message.get('content', '')
                    document_summary = _parse_formatted_content(formatter_content)
                break

    # Save structured summary to JSON if we have it
    if document_summary and work_dir:
        try:
            summary_file = os.path.join(work_dir, 'document_summary.json')
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(document_summary, f, indent=2, ensure_ascii=False)
            print(f"Document summary saved to: {summary_file}")
        except Exception as e:
            print(f"Warning: Could not save document_summary.json: {e}")

    # Pretty print the document_summary
    if document_summary:
        print(json.dumps(document_summary, indent=4))

    # Delete codebase and database folders as they are not needed
    if final_context and 'codebase_path' in final_context:
        shutil.rmtree(os.path.join(work_dir, final_context['codebase_path']), ignore_errors=True)
    if final_context and 'database_path' in final_context:
        shutil.rmtree(os.path.join(work_dir, final_context['database_path']), ignore_errors=True)

    cmbagent.display_cost()

    return document_summary


def _collect_markdown_files(folder_path: Path, max_depth: int = 10) -> List[str]:
    """Collect all markdown files from the folder and subfolders."""
    markdown_files = []

    # Use glob to find all markdown files recursively
    for ext in ['*.md', '*.markdown']:
        pattern = str(folder_path / "**" / ext)
        markdown_files.extend(glob.glob(pattern, recursive=True))

    # Filter by depth if needed
    if max_depth < float('inf'):
        filtered_files = []
        for markdown_file in markdown_files:
            relative_path = Path(markdown_file).relative_to(folder_path)
            depth = len(relative_path.parts) - 1  # -1 because the file itself is not a directory
            if depth <= max_depth:
                filtered_files.append(markdown_file)
        markdown_files = filtered_files

    return sorted(markdown_files)


def _process_single_markdown_with_error_handling(markdown_path: str,
                                                 index: int,
                                                 work_dir_base: Path,
                                                 clear_work_dir: bool,
                                                 summarizer_model: str,
                                                 summarizer_response_formatter_model: str) -> Dict[str, Any]:
    """Process a single markdown file with error handling."""
    try:
        # Create indexed work directory for this document
        work_dir = work_dir_base / f"doc_{index:03d}_{Path(markdown_path).stem}"

        # Extract arXiv ID from filename if possible
        import re
        filename = Path(markdown_path).stem
        arxiv_id_pattern = r'([0-9]+\.[0-9]+(?:v[0-9]+)?)'
        arxiv_match = re.search(arxiv_id_pattern, filename)
        arxiv_id = arxiv_match.group(1) if arxiv_match else None

        # Time the summarize_document function
        start_time = time.time()

        # Call the individual summarize_document function
        summary = summarize_document(
            markdown_document_path=markdown_path,
            work_dir=work_dir,
            clear_work_dir=clear_work_dir,
            summarizer_model=summarizer_model,
            summarizer_response_formatter_model=summarizer_response_formatter_model
        )
        end_time = time.time()
        execution_time_summarization = end_time - start_time
        print(f"Execution time summarization: {execution_time_summarization}")

        # Save the timing report
        timing_report = {
            "execution_time_summarization": execution_time_summarization,
            "arxiv_id": arxiv_id
        }
        timing_dir = os.path.join(work_dir, "time")
        os.makedirs(timing_dir, exist_ok=True)
        timing_path = os.path.join(timing_dir, "timing_report_summarization.json")
        with open(timing_path, 'w') as f:
            json.dump(timing_report, f, indent=2)
        print(f"Timing report saved to {timing_path}")

        return {
            "markdown_path": str(markdown_path),
            "index": index,
            "work_dir": str(work_dir),
            "success": True,
            "document_summary": summary,
            "filename": Path(markdown_path).name,
            "arxiv_id": arxiv_id
        }

    except Exception as e:
        # Extract arXiv ID even in error case
        import re
        filename = Path(markdown_path).stem
        arxiv_id_pattern = r'([0-9]+\.[0-9]+(?:v[0-9]+)?)'
        arxiv_match = re.search(arxiv_id_pattern, filename)
        arxiv_id = arxiv_match.group(1) if arxiv_match else None

        return {
            "markdown_path": str(markdown_path),
            "index": index,
            "success": False,
            "error": str(e),
            "filename": Path(markdown_path).name,
            "arxiv_id": arxiv_id
        }


def summarize_documents(folder_path,
                       work_dir_base=work_dir_default,
                       clear_work_dir=True,
                       summarizer_model=default_agents_llm_model['summarizer'],
                       summarizer_response_formatter_model=default_agents_llm_model['summarizer_response_formatter'],
                       max_workers=4,
                       max_depth=10):
    """
    Process multiple markdown documents in parallel, summarizing each one.

    Args:
        folder_path (str): Path to folder containing markdown files
        work_dir_base (str): Base working directory for output
        clear_work_dir (bool): Whether to clear the working directory
        summarizer_model: Model to use for summarizer agent
        summarizer_response_formatter_model: Model to use for formatter agent
        max_workers (int): Maximum number of parallel workers
        max_depth (int): Maximum depth for recursive file search

    Returns:
        Dict: Summary of processing results including individual document summaries
    """
    folder_path = Path(folder_path).resolve()

    if not folder_path.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    if not folder_path.is_dir():
        raise ValueError(f"Path is not a directory: {folder_path}")

    print(f"📁 Scanning folder: {folder_path}")
    print(f"🔍 Max depth: {max_depth}")
    print(f"👥 Max workers: {max_workers}")

    # Collect all markdown files
    markdown_files = _collect_markdown_files(folder_path, max_depth)

    if not markdown_files:
        print("⚠️ No markdown files found in the specified folder.")
        return {
            "processed_files": 0,
            "failed_files": 0,
            "total_files": 0,
            "results": [],
            "folder_path": str(folder_path),
            "work_dir_base": str(work_dir_base)
        }

    print(f"📄 Found {len(markdown_files)} markdown files")

    # Create base work directory
    work_dir_base = Path(work_dir_base).expanduser().resolve()
    work_dir_base.mkdir(parents=True, exist_ok=True)

    # Initialize results
    results = {
        "processed_files": 0,
        "failed_files": 0,
        "total_files": len(markdown_files),
        "results": [],
        "folder_path": str(folder_path),
        "work_dir_base": str(work_dir_base)
    }

    start_time = time.time()

    if clear_work_dir:
        clean_work_dir(work_dir_base)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_markdown = {
            executor.submit(
                _process_single_markdown_with_error_handling,
                markdown_path,
                i + 1,  # 1-indexed
                work_dir_base,
                clear_work_dir,
                summarizer_model,
                summarizer_response_formatter_model
            ): (markdown_path, i + 1) for i, markdown_path in enumerate(markdown_files)
        }

        # Process completed tasks
        for future in as_completed(future_to_markdown):
            markdown_path, index = future_to_markdown[future]

            try:
                result = future.result()
                if result.get("success", False):
                    results["processed_files"] += 1
                    print(f"✓ Processed [{index:02d}]: {Path(markdown_path).name}")
                else:
                    results["failed_files"] += 1
                    print(f"✗ Failed [{index:02d}]: {Path(markdown_path).name} - {result.get('error', 'Unknown error')}")

                results["results"].append(result)
            except Exception as e:
                results["failed_files"] += 1
                print(f"✗ Failed [{index:02d}]: {Path(markdown_path).name} - {str(e)}")
                results["results"].append({
                    "markdown_path": str(markdown_path),
                    "index": index,
                    "success": False,
                    "error": str(e)
                })

    end_time = time.time()
    total_time = end_time - start_time

    print(f"\n📋 Processing complete:")
    print(f"  Successfully processed: {results['processed_files']} files")
    print(f"  Failed: {results['failed_files']} files")
    print(f"  Total time: {total_time:.2f} seconds")
    print(f"  Output directory: {results['work_dir_base']}")

    # Save overall summary
    summary_file = work_dir_base / "processing_summary.json"
    try:
        results["processing_time"] = total_time
        results["timestamp"] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())

        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"📄 Overall summary saved to: {summary_file}")
    except Exception as e:
        print(f"⚠️ Warning: Could not save overall summary: {e}")

    return results


def preprocess_task(text: str,
                   work_dir: str = work_dir_default,
                   clear_work_dir: bool = True,
                   max_workers: int = 4,
                   max_depth: int = 10,
                   summarizer_model: str = default_agents_llm_model['summarizer'],
                   summarizer_response_formatter_model: str = default_agents_llm_model['summarizer_response_formatter'],
                   skip_arxiv_download: bool = False,
                   skip_ocr: bool = False,
                   skip_summarization: bool = False) -> str:
    """
    Preprocess a task description by:
    1. Extracting arXiv URLs and downloading PDFs
    2. OCRing PDFs to markdown
    3. Summarizing the papers
    4. Appending contextual information to the original text

    Args:
        text: The input task description text containing arXiv URLs
        work_dir: Working directory for processing files
        clear_work_dir: Whether to clear the work directory before starting
        max_workers: Number of parallel workers for processing
        max_depth: Maximum directory depth for file searching
        summarizer_model: Model to use for summarizer agent
        summarizer_response_formatter_model: Model to use for formatter agent
        skip_arxiv_download: Skip the arXiv download step
        skip_ocr: Skip the OCR step
        skip_summarization: Skip the summarization step

    Returns:
        str: The original text with appended "Contextual Information and References" section
    """
    from .arxiv_downloader import arxiv_filter
    from .ocr import process_folder

    print(f"🔄 Starting task preprocessing...")
    print(f"📁 Work directory: {work_dir}")

    if clear_work_dir:
        clean_work_dir(work_dir)

    # Step 1: Extract arXiv URLs and download PDFs
    arxiv_results = None
    if not skip_arxiv_download:
        print(f"📥 Step 1: Downloading arXiv papers...")
        try:
            arxiv_results = arxiv_filter(text, work_dir=work_dir)
            print(f"✅ Downloaded {arxiv_results['downloads_successful']} papers")
            print(f"📋 Total papers available: {arxiv_results['downloads_successful'] + arxiv_results['downloads_skipped']} (including previously downloaded)")

            if arxiv_results['downloads_successful'] + arxiv_results['downloads_skipped'] == 0:
                print("ℹ️ No arXiv papers found or available, skipping processing steps")
                return text
        except Exception as e:
            print(f"❌ Error downloading arXiv papers: {e}")
            return text

    # Get the docs folder path where PDFs were downloaded
    docs_folder = os.path.join(work_dir, "docs")
    if not os.path.exists(docs_folder):
        print("ℹ️ No docs folder found, returning original text")
        return text

    # Step 2: OCR PDFs to markdown
    ocr_results = None
    if not skip_ocr:
        print(f"🔍 Step 2: Converting PDFs to markdown...")
        try:
            ocr_results = process_folder(
                folder_path=docs_folder,
                save_markdown=True,
                save_json=False,  # We don't need JSON for summarization
                save_text=False,
                max_depth=max_depth,
                max_workers=max_workers,
                work_dir=work_dir
            )
            print(f"✅ OCR processed {ocr_results.get('processed_files', 0)} files")
            if ocr_results.get('processed_files', 0) == 0:
                print("ℹ️ No PDF files found for OCR, returning original text")
                return text
        except Exception as e:
            print(f"❌ Error during OCR processing: {e}")
            return text

    # Find the markdown output directory from OCR
    docs_processed_folder = docs_folder + "_processed"
    if not os.path.exists(docs_processed_folder):
        print(f"ℹ️ No processed markdown folder found at {docs_processed_folder}, returning original text")
        return text

    # Step 3: Summarize the markdown documents
    summary_results = None
    if not skip_summarization:
        print(f"📝 Step 3: Summarizing papers...")
        try:
            # Create summaries directory in the main work directory
            summaries_dir = os.path.join(work_dir, "summaries")
            summary_results = summarize_documents(
                folder_path=docs_processed_folder,
                work_dir_base=summaries_dir,
                clear_work_dir=clear_work_dir,
                max_workers=max_workers,
                max_depth=max_depth,
                summarizer_model=summarizer_model,
                summarizer_response_formatter_model=summarizer_response_formatter_model
            )
            print(f"✅ Summarized {summary_results.get('processed_files', 0)} documents")

            if summary_results.get('processed_files', 0) == 0:
                print("ℹ️ No documents were summarized, returning original text")
                return text

            # Step 4: Collect all summaries and format the contextual information
            print(f"📋 Step 4: Formatting contextual information...")
            contextual_info = []

            for result in summary_results.get('results', []):
                if result.get('success', False) and 'document_summary' in result:
                    summary = result['document_summary']
                    arxiv_id = result.get('arxiv_id')

                    # Format each summary
                    title = summary.get('title', 'Unknown Title')
                    authors = summary.get('authors', [])
                    authors_str = ', '.join(authors) if authors else 'Unknown Authors'
                    date = summary.get('date', 'Unknown Date')
                    abstract = summary.get('abstract', 'No abstract available')
                    keywords = summary.get('keywords', [])
                    keywords_str = ', '.join(keywords) if keywords else 'No keywords'
                    key_findings = summary.get('key_findings', [])

                    # Add arXiv ID if available
                    arxiv_info = f" (arXiv:{arxiv_id})" if arxiv_id else ""

                    paper_info = f"""
**{title}{arxiv_info}**
- Authors: {authors_str}
- Date: {date}
- Keywords: {keywords_str}
- Abstract: {abstract}"""

                    if key_findings:
                        paper_info += "\n- Key Findings:"
                        for finding in key_findings:
                            paper_info += f"\n  • {finding}"

                    contextual_info.append(paper_info)

            # Step 5: Append the contextual information to the original text
            if contextual_info:
                footer = "\n\n## Contextual Information and References\n"
                footer += "\n".join(contextual_info)

                enhanced_text = text + footer

                # Save enhanced text to enhanced_input.md
                enhanced_input_path = os.path.join(work_dir, "enhanced_input.md")
                try:
                    with open(enhanced_input_path, 'w', encoding='utf-8') as f:
                        f.write(enhanced_text)
                    print(f"💾 Enhanced input saved to: {enhanced_input_path}")
                except Exception as e:
                    print(f"⚠️ Warning: Could not save enhanced input: {e}")

                print(f"✅ Task preprocessing completed successfully!")
                print(f"📄 Added contextual information from {len(contextual_info)} papers")
                return enhanced_text
            else:
                print("ℹ️ No valid summaries found, returning original text")
                return text
        except Exception as e:
            print(f"❌ Error during summarization: {e}")
            return text

    return text
