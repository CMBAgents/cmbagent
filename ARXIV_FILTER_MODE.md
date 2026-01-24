# arXiv Filter Mode - Documentation

**Status**: ✅ Fully Implemented
**UI Button**: 📚 arXiv Filter (in "More Tools" dropdown)
**Mode Value**: `'arxiv'`

## Purpose

Extract arXiv URLs from text and download the corresponding PDFs **without** OCR or summarization. This is the simplest mode - just paper downloading.

## What It Does

### 1. URL Detection
Scans input text for arXiv URLs in multiple formats:
- PDF: `https://arxiv.org/pdf/2301.12345.pdf`
- Abstract: `https://arxiv.org/abs/2301.12345`
- HTML: `https://arxiv.org/html/2301.12345`
- Source: `https://arxiv.org/src/2301.12345`
- PostScript: `https://arxiv.org/ps/2301.12345`
- Versioned: `https://arxiv.org/pdf/2301.12345v2.pdf`

### 2. PDF Download
- Downloads PDFs to `work_dir/docs/` folder
- Skips already downloaded files (smart caching)
- Tracks successes, failures, and skipped downloads
- Handles network errors gracefully

### 3. Metadata Saving
Creates `docs/arxiv_download_metadata.json`:
```json
{
  "urls_found": [
    "https://arxiv.org/abs/2301.12345",
    "https://arxiv.org/pdf/2302.67890.pdf"
  ],
  "downloads_attempted": 2,
  "downloads_successful": 2,
  "downloads_failed": 0,
  "downloads_skipped": 0,
  "downloaded_files": [
    "/path/to/work_dir/docs/2301.12345.pdf",
    "/path/to/work_dir/docs/2302.67890.pdf"
  ],
  "failed_downloads": [],
  "arxiv_ids": ["2301.12345", "2302.67890"],
  "output_directory": "/path/to/work_dir/docs",
  "download_timestamp": 1706112000,
  "download_date": "2025-01-24 15:30:00"
}
```

## UI Integration

### Location
**"More Tools" Dropdown** (🔧 button) → **📚 arXiv Filter**

### Input Field
Multi-line textarea (4 rows) with placeholder:
```
"Enter text containing arXiv URLs to extract and download papers..."
```

### Example Inputs
```
Fuzzy dark matter models at https://arxiv.org/pdf/1610.08297

Recent JWST observations show Little Red Dots at https://arxiv.org/abs/2509.02664

Black hole mass measurements https://arxiv.org/html/2508.21748
```

### Configuration Panel
Shows info message:
```
ℹ️ arXiv Filter will scan your text for arXiv URLs and download
the corresponding papers to the docs folder in your work directory.
```

**No additional configuration needed** - it's a simple download operation!

## Backend Implementation

### WebSocket Handler
```python
elif mode == "arxiv":
    # arXiv Filter mode - scan text for arXiv URLs and download papers
    results = cmbagent.arxiv_filter(
        input_text=task,
        work_dir=task_work_dir
    )
```

### REST API Endpoint
```python
POST /api/arxiv/filter
Body: {
  "input_text": "Text with arXiv URLs...",
  "work_dir": "/path/to/work_dir"  # Optional
}

Response: {
  "status": "success",
  "result": { /* download metadata */ },
  "message": "Downloaded X papers successfully"
}
```

## Function Details

### Core Function
```python
cmbagent.arxiv_filter(input_text: str, work_dir: str) -> Dict[str, Any]
```

**Returns**:
```python
{
    'urls_found': List[str],              # All URLs detected
    'downloads_attempted': int,           # Total attempts
    'downloads_successful': int,          # Successful downloads
    'downloads_failed': int,              # Failed downloads
    'downloads_skipped': int,             # Already existed
    'downloaded_files': List[str],        # Full paths to PDFs
    'failed_downloads': List[Dict],       # Failed URLs with errors
    'arxiv_ids': List[str],               # Extracted arXiv IDs
    'output_directory': str,              # Where PDFs are saved
    'download_timestamp': float,          # Unix timestamp
    'download_date': str                  # Human-readable date
}
```

## Use Cases

### 1. Literature Collection
**Input**:
```
Building a dataset on CMB polarization. Papers to download:
- https://arxiv.org/abs/2301.12345
- https://arxiv.org/abs/2302.67890
- https://arxiv.org/abs/2303.11111
```

**Output**:
- 3 PDFs downloaded to `docs/`
- Metadata saved
- Ready for manual reading or further processing

### 2. Reference Extraction
**Input**:
```
The fuzzy dark matter model (arxiv:1610.08297) has gained attention.
Recent observations by JWST revealed Little Red Dots (arxiv:2509.02664).
Black hole mass measurements (arxiv:2508.21748) suggest unusual ratios.
```

**Output**:
- Automatically detects 3 papers
- Downloads all PDFs
- Can be used later for OCR or summarization

### 3. Bulk Download from List
**Input**: Paste entire reference section from a paper
**Output**: All cited arXiv papers downloaded automatically

## Comparison with Other Modes

| Feature | arXiv Filter | OCR Mode | Enhance Input |
|---------|-------------|----------|---------------|
| **Input** | Text with URLs | PDF path | Text with URLs |
| **URL Detection** | ✅ | ❌ | ✅ |
| **PDF Download** | ✅ | ❌ | ✅ |
| **OCR to Markdown** | ❌ | ✅ | ✅ |
| **Summarization** | ❌ | ❌ | ✅ |
| **Output** | PDFs + metadata | Markdown files | Enhanced text |
| **API Keys Required** | ❌ | ✅ (Mistral) | ✅ (OpenAI/Anthropic) |
| **Cost** | Free | ~$0.10/page | ~$0.15/page |
| **Speed** | Fast (seconds) | Medium (minutes) | Slow (hours) |

## Output Structure

```
work_dir/
└── docs/
    ├── 2301.12345.pdf
    ├── 2302.67890.pdf
    ├── 2303.11111.pdf
    └── arxiv_download_metadata.json
```

## Error Handling

### Common Errors

**1. No URLs Found**
```
Input: "Review fuzzy dark matter models"
Result: No downloads, empty metadata
```

**2. Invalid URL**
```
Input: "Check https://arxiv.org/invalid/12345"
Result: Logged as failed_download with error message
```

**3. Network Error**
```
Error: Connection timeout
Result: Logged in failed_downloads, continues with other URLs
```

**4. File Already Exists**
```
Output: "File '2301.12345.pdf' already exists. Skipping download."
Result: downloads_skipped += 1
```

## Advanced Features

### Smart Caching
- Checks if PDF already exists before downloading
- Useful for iterative workflows
- Saves bandwidth and time

### Parallel Processing
- Downloads happen sequentially (current implementation)
- Could be parallelized in future for faster bulk downloads

### Metadata Tracking
- Full download history saved
- Timestamps for reproducibility
- Error details for debugging

## Testing

### Quick Test
```bash
# Backend running on port 8000
curl -X POST http://localhost:8000/api/arxiv/filter \
  -H "Content-Type: application/json" \
  -d '{
    "input_text": "Check arxiv:2301.12345 for details",
    "work_dir": "/tmp/test_arxiv"
  }'
```

### UI Test
1. Click "More Tools" button
2. Select "📚 arXiv Filter"
3. Paste text with arXiv URLs
4. Click Submit
5. Watch console output stream
6. Check results panel for download summary

## Integration with Other Modes

### Sequential Workflow
```
Step 1: arXiv Filter → Download PDFs
  ↓
Step 2: OCR Mode → Convert to markdown
  ↓
Step 3: Manual analysis
```

### All-in-One Workflow
```
Use "Enhance Input" mode instead
(combines all 3 steps automatically)
```

## Console Output

### Sample Output
```
⚙️ Configuration: arXiv Filter mode - Scanning text for arXiv URLs and downloading papers

Found 3 unique arXiv URLs.
Downloading '2301.12345.pdf' from 'https://arxiv.org/pdf/2301.12345'...
Successfully downloaded and saved to '/work_dir/docs/2301.12345.pdf'.
Downloading '2302.67890.pdf' from 'https://arxiv.org/pdf/2302.67890'...
Successfully downloaded and saved to '/work_dir/docs/2302.67890.pdf'.
File '2303.11111.pdf' already exists. Skipping download.
Metadata saved to: /work_dir/docs/arxiv_download_metadata.json

=== Download Summary ===
URLs found: 3
Downloads attempted: 3
Downloads successful: 2
Downloads failed: 0
Downloads skipped (already exist): 1
Output directory: /work_dir/docs

✅ Task execution completed
```

## Results Display

### Summary Panel
Shows:
- **Total URLs found**: 3
- **Downloads successful**: 2
- **Downloads failed**: 0
- **Downloads skipped**: 1
- **Output directory**: Link to open folder

### Files Panel
Browse downloaded PDFs:
- `2301.12345.pdf` (1.2 MB)
- `2302.67890.pdf` (890 KB)
- `arxiv_download_metadata.json` (2 KB)

## Tips & Best Practices

### 1. Batch Processing
Paste entire reference lists from papers - all arXiv URLs will be detected automatically

### 2. Re-running
Safe to re-run - already downloaded files are skipped automatically

### 3. Combining Modes
Use arXiv Filter first to download, then switch to OCR mode to process the downloaded PDFs

### 4. Working Directory
Use consistent work_dir across runs to maintain a single paper repository

### 5. Metadata
Check `arxiv_download_metadata.json` to see exactly what was downloaded and when

## Limitations

### Current Implementation
- Sequential downloads (not parallel)
- No resume capability for interrupted downloads
- No automatic retry for failed downloads
- No paper metadata fetching (title, authors, etc.)

### Future Enhancements
- Parallel downloads for faster bulk processing
- Automatic metadata enrichment from arXiv API
- Smart retry with exponential backoff
- Progress bars for individual downloads
- Duplicate detection across work directories

## Troubleshooting

### Issue: No URLs detected
**Cause**: URLs not in recognized format
**Solution**: Ensure URLs start with `https://arxiv.org/`

### Issue: Downloads fail
**Cause**: Network issues or invalid arXiv ID
**Solution**: Check console output for specific error, verify arXiv ID exists

### Issue: Files disappear
**Cause**: work_dir cleared or changed
**Solution**: Use consistent work_dir, check advanced config settings

## Related Documentation

- **Enhance Input Mode**: arXiv Filter + OCR + Summarization combined
- **OCR Mode**: Process already downloaded PDFs
- **ArXiv Downloader Utils**: `cmbagent/utils/arxiv_downloader.py`

## Summary

**arXiv Filter** is the simplest document processing mode:
- ✅ No API keys required
- ✅ Free (just network bandwidth)
- ✅ Fast (seconds per paper)
- ✅ Simple output (PDFs + metadata)
- ✅ Perfect for literature collection
- ✅ Can be chained with other modes

It's the foundation for the more advanced "Enhance Input" mode, providing the first step of the pipeline: getting the papers.
