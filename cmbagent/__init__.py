# import warnings
# warnings.filterwarnings("ignore", message=r'Field "model_client_cls" in LLMConfigEntry has conflict with protected namespace "model_"')

import warnings

warnings.filterwarnings(
    "ignore",                               # action
    message=r"Update function string contains no variables\.",  # regex
    category=UserWarning,                   # same category that's raised
    module=r"autogen\.agentchat\.conversable_agent"  # where it comes from
)

# Suppress Vertex AI deprecation warning - preview module internally still uses deprecated code
warnings.filterwarnings(
    "ignore",
    message=r"This feature is deprecated as of June 24, 2025.*",
    category=UserWarning,
    module=r"vertexai\.generative_models\._generative_models"
)


from .cmbagent import CMBAgent
from .version import __version__
import os
from IPython.display import Image, display, Markdown
from autogen.cmbagent_utils import LOGO, IMG_WIDTH, cmbagent_disable_display



from .cmbagent import planning_and_control, one_shot, human_in_the_loop, control, load_plan, deep_research, work_dir_default
from .cmbagent import get_keywords, get_keywords_from_aaai, get_keywords_from_string, get_aas_keywords

# OCR functionality
from .utils.ocr import process_single_pdf, process_folder

# arXiv downloader functionality
from .utils.arxiv_downloader import arxiv_filter

# Summarization functionality
from .utils.summarization import preprocess_task, summarize_document, summarize_documents 


def print_cmbagent_logo():
    base_dir = os.path.dirname(__file__)
    # logo.png is in the images directory at the project root
    # base_dir is cmbagent/cmbagent/, so go up one level to get to cmbagent/
    project_root = os.path.dirname(base_dir)
    png_path = os.path.join(project_root, "images", "logo.png")
    # print(png_path)
    # print(base_dir)
    if not cmbagent_disable_display:
        display(Image(filename=png_path, width=IMG_WIDTH))
        display(Markdown(LOGO))
    # ANSI hyperlink: Note that support varies by terminal.
    # HTML link for GitHub with icon
    # github_html = '''
    # <a href="https://github.com/CMBAgents/cmbagent" target="_blank" style="text-decoration: none;">
    #   <img src="https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png" 
    #        alt="GitHub Icon" width="20" style="vertical-align:middle; margin-right:5px;">
    #   Star me on GitHub
    # </a>
    # '''
    # # HTML link for YouTube with icon
    # youtube_html = '''
    # <a href="https://www.youtube.com/@cmbagent" target="_blank" style="text-decoration: none;">
    #   <img src="https://upload.wikimedia.org/wikipedia/commons/4/42/YouTube_icon_%282013-2017%29.png" 
    #        alt="YouTube Icon" width="20" style="vertical-align:middle; margin-right:5px;">
    #   Watch me on YouTube
    # </a>
    # '''
    
    # display(HTML(github_html))
    # display(HTML(youtube_html))

print_cmbagent_logo()