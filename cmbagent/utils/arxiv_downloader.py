# filename: arxiv_downloader.py
import re
import os
import requests
from pathlib import Path
from typing import List, Dict, Any

from .utils import work_dir_default


class ArxivDownloader:
    """
    A class to find and download arXiv articles from a given text.
    """

    def __init__(self, work_dir: str = None):
        """
        Initializes the ArxivDownloader.

        Args:
            work_dir (str): The working directory. PDFs will be saved to work_dir/docs/
        """
        if work_dir:
            self.output_dir = os.path.join(work_dir, 'docs')
        else:
            self.output_dir = 'docs'
        self._create_output_dir()

    def _create_output_dir(self):
        """
        Creates the output directory if it does not exist.
        """
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            print("Output directory '" + self.output_dir + "' created or already exists.")
        except OSError as e:
            print("Error creating directory '" + self.output_dir + "': " + str(e))
            raise

    def _extract_urls(self, text):
        """
        Extracts all arXiv URLs from the given text.
        Supports various arXiv URL formats: pdf, abs, html, src, ps

        Args:
            text (str): The text to search for arXiv URLs.

        Returns:
            list: A list of unique arXiv URLs found in the text.
        """
        # Updated pattern to include html, src, ps, and other formats
        pattern = r'https?://arxiv\.org/(?:pdf|abs|html|src|ps)/[0-9]+\.[0-9]+(?:v[0-9]+)?(?:\.pdf)?'
        urls = re.findall(pattern, text)
        return list(set(urls))

    def download_from_text(self, text: str) -> Dict[str, Any]:
        """
        Finds all arXiv URLs in a text, downloads the corresponding PDFs,
        and saves them to the specified output directory.

        Args:
            text (str): The input text containing arXiv links.

        Returns:
            Dict[str, Any]: Summary of the download operation including:
                - urls_found: List of URLs found
                - downloads_attempted: Number of downloads attempted
                - downloads_successful: Number of successful downloads
                - downloads_failed: Number of failed downloads
                - downloads_skipped: Number of skipped downloads (already exist)
                - downloaded_files: List of successfully downloaded file paths
                - failed_downloads: List of failed download attempts with errors
                - output_directory: Path to the output directory
        """
        arxiv_urls = self._extract_urls(text)
        
        result = {
            'urls_found': arxiv_urls,
            'downloads_attempted': 0,
            'downloads_successful': 0,
            'downloads_failed': 0,
            'downloads_skipped': 0,
            'downloaded_files': [],
            'failed_downloads': [],
            'arxiv_ids': [],  # Track arXiv IDs
            'output_directory': self.output_dir
        }

        if not arxiv_urls:
            print("No arXiv URLs found in the provided text.")
            return result

        print(f"Found {len(arxiv_urls)} unique arXiv URLs.")

        id_pattern = r'([0-9]+\.[0-9]+(?:v[0-9]+)?)'

        for url in arxiv_urls:
            result['downloads_attempted'] += 1
            
            try:
                match = re.search(id_pattern, url)
                if not match:
                    error_msg = f"Could not extract article ID from URL: {url}"
                    print(error_msg)
                    result['downloads_failed'] += 1
                    result['failed_downloads'].append({'url': url, 'error': error_msg})
                    continue
                
                article_id = match.group(1)
                pdf_url = f'https://arxiv.org/pdf/{article_id}'
                filename = f'{article_id}.pdf'
                filepath = os.path.join(self.output_dir, filename)

                if os.path.exists(filepath):
                    print(f"File '{filename}' already exists. Skipping download.")
                    result['downloads_skipped'] += 1
                    result['arxiv_ids'].append(article_id)  # Track ID even if skipped
                    continue

                print(f"Downloading '{filename}' from '{pdf_url}'...")

                response = requests.get(pdf_url, stream=True, timeout=30)
                response.raise_for_status()

                with open(filepath, 'wb') as f:
                    f.write(response.content)

                print(f"Successfully downloaded and saved to '{filepath}'.")
                result['downloads_successful'] += 1
                result['downloaded_files'].append(filepath)
                result['arxiv_ids'].append(article_id)

            except requests.exceptions.HTTPError as e:
                error_msg = f"HTTP Error: {str(e)}"
                print(f"Failed to download from '{url}'. {error_msg}")
                result['downloads_failed'] += 1
                result['failed_downloads'].append({'url': url, 'error': error_msg})
            except requests.exceptions.RequestException as e:
                error_msg = f"Request Error: {str(e)}"
                print(f"Failed to download from '{url}'. {error_msg}")
                result['downloads_failed'] += 1
                result['failed_downloads'].append({'url': url, 'error': error_msg})
            except IOError as e:
                error_msg = f"File I/O Error: {str(e)}"
                print(f"Failed to save file for '{url}'. {error_msg}")
                result['downloads_failed'] += 1
                result['failed_downloads'].append({'url': url, 'error': error_msg})
            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                print(f"An unexpected error occurred for URL '{url}': {error_msg}")
                result['downloads_failed'] += 1
                result['failed_downloads'].append({'url': url, 'error': error_msg})

        # Save metadata to JSON file
        if self.output_dir:
            metadata_file = os.path.join(self.output_dir, 'arxiv_download_metadata.json')
            try:
                # Add timestamp to metadata
                import time
                result['download_timestamp'] = time.time()
                result['download_date'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
                
                with open(metadata_file, 'w', encoding='utf-8') as f:
                    import json
                    json.dump(result, f, indent=2, ensure_ascii=False)
                print(f"Metadata saved to: {metadata_file}")
            except Exception as e:
                print(f"Warning: Could not save metadata to {metadata_file}: {str(e)}")

        # Print summary
        print(f"\n=== Download Summary ===")
        print(f"URLs found: {len(result['urls_found'])}")
        print(f"Downloads attempted: {result['downloads_attempted']}")
        print(f"Downloads successful: {result['downloads_successful']}")
        print(f"Downloads failed: {result['downloads_failed']}")
        print(f"Downloads skipped (already exist): {result['downloads_skipped']}")
        print(f"Output directory: {result['output_directory']}")

        return result


# User-facing convenience function
def arxiv_filter(input_text: str, work_dir = work_dir_default) -> Dict[str, Any]:
    """
    Extract all arXiv URLs from input text and download the corresponding PDFs 
    to the docs folder inside the work directory.
    
    Args:
        input_text (str): Text containing arXiv URLs to extract and download
        work_dir (str): Working directory where docs/ folder will be created.
                       Defaults to cmbagent's standard work directory.
    
    Returns:
        Dict[str, Any]: Summary of the download operation including:
            - urls_found: List of URLs found
            - downloads_attempted: Number of downloads attempted  
            - downloads_successful: Number of successful downloads
            - downloads_failed: Number of failed downloads
            - downloads_skipped: Number of skipped downloads (already exist)
            - downloaded_files: List of successfully downloaded file paths
            - failed_downloads: List of failed download attempts with errors
            - output_directory: Path to the output directory
    """
    downloader = ArxivDownloader(work_dir=str(work_dir) if work_dir else None)
    return downloader.download_from_text(input_text)


if __name__ == '__main__':
    input_text = """
    We are in the context of fuzzy dark matter (FDM) models, also known as ultralight or scalar dark matter, a particular dark matter (DM) candidate (see https://arxiv.org/pdf/1610.08297). The idea to develop is to calculate whether or not FDM can explain some of the properties of Little Red Dots (LRDs) which have been recently discovered by JWST. LRDs are astrophysical objects with very peculiar properties. Little red dots (LRDs) are a newly identified class of broad-line active galactic nuclei (AGN) with a distinctive v-shape spectrum characterized by red optical and blue UV continuum emission. Their high abundance at redshifts of z∼6−8 and decline at lower redshifts suggest a transient origin. Most likely, they are the cradle of supermassive black holes (see https://arxiv.org/pdf/2509.02664). We ask to focus on the possibility that the large mass inferred for the Black Hole (BH) could be produced by the accretion of a FDM solitonic core. For example in this paper here https://arxiv.org/abs/2508.21748 presents a tentative measurement of the BH mass and also on the ratio of BH mass with the stellar mass, this ratio has to be larger than 2. Such a ''naked'' black hole, together with its near-pristine environment, indicates that this LRD is a massive black hole seed caught in its earliest accretion phase, with a lot of surrounding gas. We would like to explore possibilities in the context of FDM models, both at the level of DM accretion rates/times, or dynamical effects. For example, see https://arxiv.org/abs/1908.04790.
    """
    
    result = arxiv_filter(input_text, work_dir='test_downloads')
    print(f"\nTest completed! Downloaded {result['downloads_successful']} papers.")
