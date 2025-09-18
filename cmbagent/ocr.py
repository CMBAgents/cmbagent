"""
OCR the pdf file with Mistral OCR
"""

import os
import re
import json
import time
import base64
from pathlib import Path
from typing import Dict, List, Any, Optional
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import glob

# Mistral AI imports
from mistralai import Mistral, DocumentURLChunk
from mistralai.extra import response_format_from_pydantic_model


from pydantic import BaseModel, Field
from enum import Enum

from .utils import get_api_keys_from_env

class ImageType(str, Enum):
    GRAPH = "graph"
    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"

class Image(BaseModel):
    image_type: ImageType = Field(..., description="The type of the image. Must be one of 'graph', 'text', 'table' or 'image'.")
    description: str = Field(..., description="A description of the image.")
    label: str = Field(...,description="the label of the image, i.e., number, letter, as it is refered in text")


class MistralOCRProcessor:
    """Process PDFs with Mistral OCR API and prepare for PaperQA2."""
    
    def __init__(self, api_key=None):
        """Initialize the OCR processor."""
        if api_key is None:
            api_keys = get_api_keys_from_env()
            api_key = api_keys.get("MISTRAL")
        
        if not api_key:
            raise ValueError("MISTRAL_API_KEY environment variable is required")
        self.client = Mistral(api_key=api_key)


    def process_folder(self, 
                       folder_path: str, 
                       save_markdown: bool = True, 
                       save_json: bool = True, 
                       save_text: bool = False,
                       output_dir: str = None,
                       meta_data_path: str = None,
                       max_depth: int = 10,
                       max_workers: int = 4,
                       work_dir: str = None) -> Dict[str, Any]:
        """
        Process all PDF files in a folder and its subfolders.
        
        Args:
            folder_path: Path to the folder containing PDF files
            save_markdown: Whether to save markdown files
            save_json: Whether to save JSON files
            save_text: Whether to save text files
            output_dir: Directory to save the output files (default: folder_path + "_processed")
            meta_data_path: Path to the metadata file
            max_depth: Maximum depth to search for PDF files
            max_workers: Number of parallel workers for processing
            work_dir: Working directory for cost tracking (optional)
        
        Returns:
            Dictionary with processing results summary
        """
        folder_path = Path(folder_path)
        
        if not folder_path.exists():
            raise FileNotFoundError(f"Folder not found: {folder_path}")
        
        # Set up output directory
        if output_dir is None:
            if work_dir is not None:
                # Use work_dir with source folder name + _processed
                source_name = folder_path.name
                output_dir = Path(work_dir) / f"{source_name}_processed"
            else:
                # Original behavior: same level as source with _processed
                output_dir = folder_path.parent / f"{folder_path.name}_processed"
        else:
            output_dir = Path(output_dir)
        
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. Create directory tree listing
        print(f"Scanning directory tree for PDF files...")
        dir_tree = self._list_dir_tree(folder_path, max_depth)
        
        # 2. Collect all PDF files
        pdf_files = self._collect_pdf_files(folder_path, max_depth)
        
        if not pdf_files:
            print(f"No PDF files found in {folder_path}")
            return {
                "processed_files": 0,
                "failed_files": 0,
                "output_directory": str(output_dir),
                "pdf_files": []
            }
        
        print(f"Found {len(pdf_files)} PDF files to process")
        
        # 3. Process PDF files in parallel
        results = {
            "processed_files": 0,
            "failed_files": 0,
            "output_directory": str(output_dir),
            "pdf_files": pdf_files,
            "results": []
        }
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_pdf = {
                executor.submit(
                    self._process_pdf_with_error_handling,
                    pdf_path,
                    save_markdown,
                    save_json,
                    save_text,
                    output_dir,
                    meta_data_path,
                    work_dir
                ): pdf_path for pdf_path in pdf_files
            }
            
            # Process completed tasks
            for future in as_completed(future_to_pdf):
                pdf_path = future_to_pdf[future]
                try:
                    result = future.result()
                    if result["success"]:
                        results["processed_files"] += 1
                        print(f"✓ Processed: {Path(pdf_path).name}")
                    else:
                        results["failed_files"] += 1
                        print(f"✗ Failed: {Path(pdf_path).name} - {result['error']}")
                    
                    results["results"].append(result)
                except Exception as e:
                    results["failed_files"] += 1
                    print(f"✗ Failed: {Path(pdf_path).name} - {str(e)}")
                    results["results"].append({
                        "pdf_path": str(pdf_path),
                        "success": False,
                        "error": str(e)
                    })
        
        print(f"\nProcessing complete:")
        print(f"  Successfully processed: {results['processed_files']} files")
        print(f"  Failed: {results['failed_files']} files")
        print(f"  Output directory: {results['output_directory']}")
        
        return results
    
    def _list_dir_tree(self, startpath: Path, max_depth: int = 10) -> List[str]:
        """Create a list tree of the directory structure."""
        tree = []
        
        def _build_tree(path: Path, prefix: str = "", depth: int = 0):
            if depth > max_depth:
                return
            
            try:
                items = sorted(path.iterdir())
                dirs = [item for item in items if item.is_dir()]
                files = [item for item in items if item.is_file()]
                
                # Add directories first
                for i, item in enumerate(dirs):
                    is_last_dir = i == len(dirs) - 1 and len(files) == 0
                    tree.append(f"{prefix}{'└── ' if is_last_dir else '├── '}{item.name}/")
                    next_prefix = prefix + ("    " if is_last_dir else "│   ")
                    _build_tree(item, next_prefix, depth + 1)
                
                # Add files
                for i, item in enumerate(files):
                    is_last = i == len(files) - 1
                    tree.append(f"{prefix}{'└── ' if is_last else '├── '}{item.name}")
                    
            except PermissionError:
                tree.append(f"{prefix}[Permission Denied]")
        
        tree.append(f"{startpath.name}/")
        _build_tree(startpath, "", 0)
        return tree
    
    def _collect_pdf_files(self, folder_path: Path, max_depth: int = 10) -> List[str]:
        """Collect all PDF files from the folder and subfolders."""
        pdf_files = []
        
        # Use glob to find all PDF files recursively
        pattern = str(folder_path / "**" / "*.pdf")
        pdf_files = glob.glob(pattern, recursive=True)
        
        # Filter by depth if needed
        if max_depth < float('inf'):
            filtered_files = []
            for pdf_file in pdf_files:
                relative_path = Path(pdf_file).relative_to(folder_path)
                depth = len(relative_path.parts) - 1  # -1 because the file itself is not a directory
                if depth <= max_depth:
                    filtered_files.append(pdf_file)
            pdf_files = filtered_files
        
        return sorted(pdf_files)
    
    def _process_pdf_with_error_handling(self, 
                                        pdf_path: str,
                                        save_markdown: bool,
                                        save_json: bool,
                                        save_text: bool,
                                        output_dir: Path,
                                        meta_data_path: str,
                                        work_dir: str = None) -> Dict[str, Any]:
        """Process a single PDF with error handling for parallel execution."""
        try:
            # Write files directly to the main output directory (no subfolders)
            result = self.process_single_pdf(
                pdf_path=pdf_path,
                save_markdown=save_markdown,
                save_json=save_json,
                save_text=save_text,
                output_dir=str(output_dir),
                meta_data_path=meta_data_path,
                work_dir=work_dir
            )
            
            return {
                "pdf_path": pdf_path,
                "success": True,
                "output_directory": str(output_dir),
                "num_pages": result.get("num_pages", 0),
                "error": None
            }
            
        except Exception as e:
            return {
                "pdf_path": pdf_path,
                "success": False,
                "output_directory": None,
                "num_pages": 0,
                "error": str(e)
            }

    def process_single_pdf(self, 
                           pdf_path: str, 
                           save_markdown: bool = True, 
                           save_json: bool = True, 
                           save_text: bool = False,
                           output_dir: str = None,
                           meta_data_path: str = None,
                           work_dir: str = None) -> Dict[str, Any]:
        """
        Process a single PDF file with Mistral OCR.
        
        Args:
            pdf_path: Path to the PDF file
            save_markdown: Whether to save markdown files
            save_json: Whether to save JSON files
            save_text: Whether to save text files
            output_dir: Directory to save the output files
            meta_data_path: Path to the metadata file
            work_dir: Working directory for cost tracking (optional)
        Returns:
            Dictionary with extracted text by page and cost information
        """
        pdf_file = Path(pdf_path)

        # if output dir is not specified, use work_dir structure or default behavior
        if output_dir is None:
            if work_dir is not None:
                # Use work_dir with source name + _processed
                source_name = pdf_file.stem
                output_dir = os.path.join(work_dir, f"{source_name}_processed")
            else:
                # Original behavior: same level as source with _processed
                output_dir = os.path.join(pdf_file.parent, f"{pdf_file.stem}_processed")
        
        # Always ensure the output directory exists
        os.makedirs(output_dir, exist_ok=True)





        if not pdf_file.is_file():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        
        # Upload PDF file to Mistral's OCR service
        try:
            # print(f"Uploading PDF: {pdf_file.name}")
            # uploaded_file = self.client.files.upload(
            #     file={
            #         "file_name": pdf_file.stem,
            #         "content": pdf_file.read_bytes(),
            #     },
            #     purpose="ocr",
            # )
            
            # # Get URL for the uploaded file
            # signed_url = self.client.files.get_signed_url(
            #     file_id=uploaded_file.id, 
            #     expiry=60
            # )
            print(f"Encoding PDF: {pdf_path}")
            base64_pdf = self._encode_pdf(pdf_path)
            
            # Process PDF with OCR
            print("Processing PDF with OCR...")
            ocr_response = self.client.ocr.process(
            document={
                "type": "document_url",
                "document_url": f"data:application/pdf;base64,{base64_pdf}" 
            },
                model="mistral-ocr-latest",
                include_image_base64=False,
                bbox_annotation_format=response_format_from_pydantic_model(Image),
            )
            
            # Extract usage information and calculate cost
            usage_info = ocr_response.usage_info if hasattr(ocr_response, 'usage_info') else None
            cost_info = self._calculate_cost_info(usage_info, pdf_file.name)
            
            # Save cost information to work_dir if provided
            if work_dir is not None:
                self._save_cost_info(cost_info, work_dir)
            
            # Extract structured content
            structured_content = self._extract_structured_content(ocr_response, pdf_file.stem)
            
            # Add cost information to the result
            structured_content["cost_info"] = cost_info
            
            # Save outputs
            base_name = pdf_file.stem
            
            if save_json:
                json_path = os.path.join(output_dir, f"{base_name}_ocr.json")
                self._save_to_json(structured_content, json_path)
            
            if save_markdown:
                markdown_path = os.path.join(output_dir, f"{base_name}.md")
                self._save_to_markdown(structured_content, markdown_path)
            
            # Also create PaperQA2 compatible text file if save_text is True    
            if save_text:
                txt_path = os.path.join(output_dir, f"{base_name}.txt")
                self._save_to_text(structured_content, txt_path)
            
            return structured_content
            
        except Exception as e:
            print(f"Error processing PDF: {str(e)}")
            raise
        
    def _encode_pdf(self, pdf_path):
        """Encode the pdf to base64."""
        try:
            with open(pdf_path, "rb") as pdf_file:
                return base64.b64encode(pdf_file.read()).decode('utf-8')
        except FileNotFoundError:
            print(f"Error: The file {pdf_path} was not found.")
            return None
        except Exception as e:  # Added general exception handling
            print(f"Error: {e}")
            return None


    def _extract_structured_content(self, ocr_response, pdf_name: str) -> Dict[str, Any]:
        """Extract structured content from OCR response."""
        structured_content = {
            "filename": pdf_name,
            "num_pages": len(ocr_response.pages),
            "pages": [],
            "sections": [],
            "full_text": "",
            "full_markdown": ""
        }
        
        # Process each page
        full_text = []
        full_markdown = []
        current_section = None
        section_content = []
        
        for i, page in enumerate(ocr_response.pages):
            page_num = i + 1
            page_markdown = page.markdown
            page_text = page.text if hasattr(page, 'text') else page_markdown
            
            full_text.append(page_text)
            full_markdown.append(page_markdown)
            
            # Store page content
            structured_content["pages"].append({
                "page_num": page_num,
                "text": page_text,
                "markdown": page_markdown
            })
            
            # Try to identify sections
            lines = page_markdown.split("\n")
            for line in lines:
                # Check for section headers
                section_match = re.match(r'^#{1,3}\s*(\d+\.(?:\d+)?)\s+([A-Z][a-zA-Z\s]+)$', line) or \
                               re.match(r'^(\d+\.(?:\d+)?)\s+([A-Z][a-zA-Z\s]+)$', line)
                
                if section_match:
                    # Save previous section
                    if current_section:
                        structured_content["sections"].append({
                            "section_id": current_section,
                            "content": "\n".join(section_content)
                        })
                    
                    # Start new section
                    current_section = section_match.group(1)
                    section_title = section_match.group(2)
                    section_content = [f"{current_section} {section_title}"]
                else:
                    if current_section:
                        section_content.append(line)
        
        # Add the last section
        if current_section and section_content:
            structured_content["sections"].append({
                "section_id": current_section,
                "content": "\n".join(section_content)
            })
        
        # Combine all content
        structured_content["full_text"] = "\n\n".join(full_text)
        structured_content["full_markdown"] = "\n\n".join(full_markdown)
        
        return structured_content

    def _save_to_json(self, data: Dict[str, Any], output_path: str) -> None:
        """Save the structured content to a JSON file."""
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"Saved JSON output to {output_path}")
        except Exception as e:
            print(f"Error saving JSON output: {str(e)}")
            raise

    def _save_to_markdown(self, data: Dict[str, Any], output_path: str) -> None:
        """Save the full markdown content to a .md file."""
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                # Add title
                f.write(f"# {data['filename']}\n\n")
                f.write(f"**Pages:** {data['num_pages']}\n\n")
                f.write("---\n\n")
                f.write(data["full_markdown"])
            print(f"Saved Markdown output to {output_path}")
        except Exception as e:
            print(f"Error saving Markdown output: {str(e)}")
            raise

    def _save_to_text(self, data: Dict[str, Any], output_path: str) -> None:
        """Save as plain text for PaperQA2 compatibility."""
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(data["full_text"])
            print(f"Saved text output to {output_path}")
        except Exception as e:
            print(f"Error saving text output: {str(e)}")
            raise

    def _calculate_cost_info(self, usage_info, filename: str) -> Dict[str, Any]:
        """Calculate cost information from Mistral OCR usage data."""
        if usage_info is None:
            return {
                "filename": filename,
                "pages_processed": 0,
                "doc_size_bytes": 0,
                "cost_usd": 0.0,
                "cost_per_page": 0.001,  # $1 per 1000 pages
                "timestamp": time.time()
            }
        
        pages_processed = usage_info.pages_processed if hasattr(usage_info, 'pages_processed') else 0
        doc_size_bytes = usage_info.doc_size_bytes if hasattr(usage_info, 'doc_size_bytes') else 0
        
        # Cost calculation: $1 per 1000 pages
        cost_usd = (pages_processed / 1000.0) * 1.0
        
        return {
            "filename": filename,
            "pages_processed": pages_processed,
            "doc_size_bytes": doc_size_bytes,
            "cost_usd": cost_usd,
            "cost_per_page": 0.001,
            "timestamp": time.time()
        }

    def _save_cost_info(self, cost_info: Dict[str, Any], work_dir: str) -> None:
        """Save cost information to ocr_cost.json in work directory."""
        cost_file_path = os.path.join(work_dir, "ocr_cost.json")
        
        # Load existing cost data if file exists
        existing_costs = []
        if os.path.exists(cost_file_path):
            try:
                with open(cost_file_path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                    if isinstance(existing_data, dict) and 'entries' in existing_data:
                        # Existing cost summary format - extract entries
                        existing_costs = existing_data['entries']
                    elif isinstance(existing_data, list):
                        # Legacy list format
                        existing_costs = existing_data
                    elif isinstance(existing_data, dict):
                        # Single entry format
                        existing_costs = [existing_data]
            except Exception as e:
                print(f"Warning: Could not read existing cost file: {e}")
        
        # Append new cost info
        existing_costs.append(cost_info)
        
        # Calculate total costs
        total_pages = sum(item.get("pages_processed", 0) for item in existing_costs)
        total_cost = sum(item.get("cost_usd", 0.0) for item in existing_costs)
        
        # Create summary with individual entries
        cost_summary = {
            "total_pages_processed": total_pages,
            "total_cost_usd": total_cost,
            "cost_per_page": 0.001,
            "entries": existing_costs,
            "last_updated": time.time()
        }
        
        try:
            with open(cost_file_path, 'w', encoding='utf-8') as f:
                json.dump(cost_summary, f, indent=2, ensure_ascii=False)
            print(f"Updated cost tracking: {cost_file_path}")
            print(f"Session total: {total_pages} pages, ${total_cost:.3f}")
        except Exception as e:
            print(f"Error saving cost information: {str(e)}")


# User-facing convenience functions
def process_single_pdf(pdf_path: str, 
                      save_markdown: bool = True, 
                      save_json: bool = True, 
                      save_text: bool = False,
                      output_dir: str = None,
                      meta_data_path: str = None,
                      work_dir: str = None):
    """
    Process a single PDF file with Mistral OCR.
    
    Args:
        pdf_path: Path to the PDF file
        save_markdown: Whether to save markdown files
        save_json: Whether to save JSON files
        save_text: Whether to save text files
        output_dir: Directory to save the output files
        meta_data_path: Path to the metadata file
        work_dir: Working directory for cost tracking (optional)
    
    Returns:
        Dictionary with extracted text by page and cost information
    """
    processor = MistralOCRProcessor()
    return processor.process_single_pdf(
        pdf_path=pdf_path,
        save_markdown=save_markdown,
        save_json=save_json,
        save_text=save_text,
        output_dir=output_dir,
        meta_data_path=meta_data_path,
        work_dir=work_dir
    )

def process_folder(folder_path: str, 
                   save_markdown: bool = True, 
                   save_json: bool = True, 
                   save_text: bool = False,
                   output_dir: str = None,
                   meta_data_path: str = None,
                   max_depth: int = 10,
                   max_workers: int = 4,
                   work_dir: str = None):
    """
    Process all PDF files in a folder and its subfolders.
    
    Args:
        folder_path: Path to the folder containing PDF files
        save_markdown: Whether to save markdown files
        save_json: Whether to save JSON files
        save_text: Whether to save text files
        output_dir: Directory to save the output files (default: folder_path + "_processed")
        meta_data_path: Path to the metadata file
        max_depth: Maximum depth to search for PDF files
        max_workers: Number of parallel workers for processing
        work_dir: Working directory for cost tracking (optional)
    
    Returns:
        Dictionary with processing results summary
    """
    processor = MistralOCRProcessor()
    return processor.process_folder(
        folder_path=folder_path,
        save_markdown=save_markdown,
        save_json=save_json,
        save_text=save_text,
        output_dir=output_dir,
        meta_data_path=meta_data_path,
        max_depth=max_depth,
        max_workers=max_workers,
        work_dir=work_dir
    )

