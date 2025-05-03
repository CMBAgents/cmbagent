#!/bin/bash
# installer.sh  ── set up a venv, install ag2 + cmbagent (specific branches),
# create a .env with the required API keys, and add a Jupyter kernel.
#
# USAGE
#   ./installer.sh           # normal install
#   ./installer.sh clean     # remove venv and cloned repos
# ---------------------------------------------------------------------------

###############################################################################
# 1. optional clean-up
###############################################################################
if [ "$1" == "clean" ]; then
  echo "Cleaning up Python virtual environment and repositories..."
  rm -rf astrop_env ag2 cmbagent
  echo "Clean operation completed."
  exit 0
fi

export GIT_LFS_SKIP_SMUDGE=1
set -e  # stop on first error

###############################################################################
# 2. ensure git-lfs is available (needed for ag2’s large notebooks/media)
###############################################################################
if ! command -v git-lfs &>/dev/null; then
  echo "git-lfs not found – installing..."
  if command -v brew &>/dev/null; then
    brew install git-lfs
  elif command -v apt-get &>/dev/null; then
    sudo apt-get update && sudo apt-get install -y git-lfs
  else
    echo "Please install Git LFS manually from https://git-lfs.github.com/ and re-run."
    exit 1
  fi
fi
git lfs install            # (idempotent) sets up hooks for the current user

###############################################################################
# 3. helper
###############################################################################
clone_repo () {
  local url=$1 dir=$2
  if [ ! -d "$dir" ]; then
    echo "Cloning $url ..."
    git clone "$url"
  else
    echo "Directory $dir already exists – skipping clone."
  fi
}

###############################################################################
# 4. Python virtual environment
###############################################################################
echo "Creating Python 3.12 virtual environment..."
python3.12 -m venv astrop_env
echo "Activating virtual environment..."
# shellcheck disable=SC1091
source astrop_env/bin/activate

echo "Upgrading pip..."
python -m pip install --upgrade pip

###############################################################################
###############################################################################
# 5. ag2
###############################################################################
AG2_REPO="https://github.com/CMBAgents/ag2.git"
AG2_DIR="ag2"

# Clone once (skipping LFS blobs), then on every run fetch+clean+force-checkout
if [ ! -d "$AG2_DIR" ]; then
  echo "Cloning ag2 (skipping LFS downloads)…"
  GIT_LFS_SKIP_SMUDGE=1 git clone "$AG2_REPO" "$AG2_DIR"
fi

echo "Fetching, cleaning, and force-switching ag2 → ag2_v0.8.4_upgrade_astrop…"
cd "$AG2_DIR"
git fetch origin
git clean -fdx
git switch -C ag2_v0.8.4_upgrade_astrop origin/ag2_v0.8.4_upgrade_astrop

echo "Installing ag2 in editable mode…"
pip install -e .
cd ..


###############################################################################
# 6. cmbagent
###############################################################################
CMBAGENT_REPO="https://github.com/CMBAgents/cmbagent.git"
CMBAGENT_DIR="cmbagent"

# Clone once, then always fetch+clean+force-checkout
if [ ! -d "$CMBAGENT_DIR" ]; then
  echo "Cloning cmbagent…"
  git clone "$CMBAGENT_REPO" "$CMBAGENT_DIR"
fi

echo "Fetching, cleaning, and force-switching cmbagent → astrop…"
cd "$CMBAGENT_DIR"
git fetch origin
git clean -fdx
git switch -C astrop origin/astrop

echo "Installing cmbagent in editable mode…"
pip install -e .
cd ..


###############################################################################
# 7. environment variables → .env
###############################################################################
echo "Creating .env file with API keys..."
cat <<EOF > .env
OPENAI_API_KEY="${OPENAI_API_KEY}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}"
GEMINI_API_KEY="${GEMINI_API_KEY}"
PERPLEXITY_API_KEY="${PERPLEXITY_API_KEY}"
EOF
echo ".env file created at $(pwd)/.env"

###############################################################################
# 8. extra Python packages
###############################################################################
pip install pdflatex
pip install jsonschema==4.23.0

###############################################################################
# 9. Jupyter kernel
###############################################################################
echo "Registering IPython kernel..."
pip install ipykernel
python -m ipykernel install --user --name astrop_env --display-name "Python (astrop_env)"
echo "Kernel registered. You can now select 'Python (astrop_env)' in Jupyter Notebook."
echo "Installing streamlit..."
pip install streamlit

echo "Installation complete!"
