#!/bin/sh
set -e

REPO="https://raw.githubusercontent.com/alastor0325/matrix-cli/main"
BIN_DIR="${HOME}/.local/bin"
SCRIPT="${BIN_DIR}/matrix-cli"
SHIM="${BIN_DIR}/matrix-notify"

# Create bin dir if needed
mkdir -p "${BIN_DIR}"

# Download the script
echo "Downloading matrix-cli..."
curl -fsSL "${REPO}/matrix-cli" -o "${SCRIPT}"
chmod +x "${SCRIPT}"

# Backwards-compatibility shim
printf '#!/bin/sh\nexec matrix-cli notify "$@"\n' > "${SHIM}"
chmod +x "${SHIM}"

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
