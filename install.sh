#!/bin/sh
set -e

REPO="https://raw.githubusercontent.com/alastor0325/matrix-cli/main"
BIN_DIR="${HOME}/.local/bin"
CONFIG_DIR="${HOME}/.matrix-cli"
STORE="${CONFIG_DIR}/matrix-cli"
SHIM="${BIN_DIR}/matrix-notify"

# Create dirs if needed
mkdir -p "${BIN_DIR}" "${CONFIG_DIR}"

# Download the script to its permanent store location
echo "Downloading matrix-cli..."
curl -fsSL "${REPO}/matrix-cli" -o "${STORE}"
chmod +x "${STORE}"

# If setup has already been run (venv exists), regenerate the bin shim now
# so that upgrading via this script doesn't require re-running setup.
VENV="${CONFIG_DIR}/.venv"
if [ -f "${VENV}/bin/python3" ]; then
    printf '#!/bin/sh\nexec "%s/bin/python3" "%s" "$@"\n' "${VENV}" "${STORE}" > "${BIN_DIR}/matrix-cli"
    chmod +x "${BIN_DIR}/matrix-cli"
else
    # First install: put the raw script in bin so `matrix-cli` (setup) works
    cp "${STORE}" "${BIN_DIR}/matrix-cli"
    chmod +x "${BIN_DIR}/matrix-cli"
fi

# Backwards-compatibility shim
printf '#!/bin/sh\nexec matrix-cli notify "$@"\n' > "${SHIM}"
chmod +x "${SHIM}"

# Check python3-venv is available (required for setup)
if ! python3 -c "import ensurepip" 2>/dev/null; then
    VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    echo ""
    echo "  python3-venv is required but not installed."
    echo "  On Debian/Ubuntu, run:  sudo apt install python3-venv"
    echo "  On Fedora/RHEL, run:    sudo dnf install python3-venv"
    echo ""
    exit 1
fi

# Install requests if missing
if ! python3 -c "import requests" 2>/dev/null; then
    echo "Installing requests..."
    pip3 install --quiet requests
fi

# Warn if bin dir is not on PATH
case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *)
        echo ""
        echo "  Add this to your shell profile to put matrix-cli on PATH:"
        echo "    export PATH=\"\${HOME}/.local/bin:\${PATH}\""
        echo ""
        ;;
esac

echo "Done. Run 'matrix-cli' to complete setup."
