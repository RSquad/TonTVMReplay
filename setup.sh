#!/bin/bash
set -e  # Exit on any error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================"
echo "TonTVMReplay Setup Script"
echo "================================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ==============================================================================
# Step 1: Setup Rust Emulator
# ==============================================================================
echo -e "${YELLOW}[1/3] Checking Rust emulator...${NC}"

if [ -d "rust" ] && [ -f "rust/libemulator.so" ]; then
    echo -e "${GREEN}✓ Rust emulator already exists at rust/libemulator.so${NC}"
else
    echo -e "${YELLOW}Building Rust emulator from RSquad/ton-node...${NC}"
    
    # Check if Rust/Cargo is installed
    if ! command -v cargo &> /dev/null; then
        echo -e "${RED}ERROR: Cargo (Rust) is not installed!${NC}"
        echo "Please install Rust from https://rustup.rs/"
        echo "Run: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
        exit 1
    fi
    
    # Create rust directory if it doesn't exist
    mkdir -p rust
    
    # Clone repository
    TEMP_RUST_DIR=$(mktemp -d)
    echo "Cloning RSquad/ton-node to $TEMP_RUST_DIR..."
    git clone --branch tvm-emulator0 git@github.com:RSquad/ton-node.git "$TEMP_RUST_DIR"
    
    # Build emulator
    echo "Building emulator (this may take several minutes)..."
    cd "$TEMP_RUST_DIR"
    cargo build -p emulator --release
    
    # Copy library
    if [ -f "target/release/libemulator.so" ]; then
        cp target/release/libemulator.so "$SCRIPT_DIR/rust/libemulator.so"
        echo -e "${GREEN}✓ Rust emulator built and copied to rust/libemulator.so${NC}"
    else
        echo -e "${RED}ERROR: Failed to build Rust emulator (libemulator.so not found)${NC}"
        exit 1
    fi
    
    # Cleanup
    cd "$SCRIPT_DIR"
    rm -rf "$TEMP_RUST_DIR"
    echo "Cleaned up temporary build directory"
fi

echo ""

# ==============================================================================
# Step 2: Setup C++ Emulator
# ==============================================================================
echo -e "${YELLOW}[2/3] Checking C++ emulator...${NC}"

if [ -d "cpp" ] && [ -f "cpp/libemulator.so" ]; then
    echo -e "${GREEN}✓ C++ emulator already exists at cpp/libemulator.so${NC}"
else
    echo -e "${YELLOW}Building C++ emulator from ton-blockchain/ton...${NC}"
    
    # Check for required build tools
    if ! command -v cmake &> /dev/null; then
        echo -e "${RED}ERROR: cmake is not installed!${NC}"
        echo "Install with: sudo apt-get install cmake"
        exit 1
    fi
    
    if ! command -v g++ &> /dev/null; then
        echo -e "${RED}ERROR: g++ is not installed!${NC}"
        echo "Install with: sudo apt-get install build-essential"
        exit 1
    fi
    
    # Create cpp directory if it doesn't exist
    mkdir -p cpp
    
    # Install dependencies (Ubuntu/Debian)
    echo "Installing dependencies..."
    
    if command -v apt-get &> /dev/null; then
        sudo apt-get update
        sudo apt-get install -y build-essential cmake openssl libssl-dev \
            zlib1g-dev gperf wget git curl libreadline-dev ccache libmicrohttpd-dev \
            pkg-config libsodium-dev libsecp256k1-dev liblz4-dev ninja-build
        
        # Install LLVM/Clang 21 (required for TON compilation)
        echo "Installing LLVM/Clang 21..."
        if ! command -v clang-21 &> /dev/null; then
            # Download and run LLVM installation script
            LLVM_SCRIPT=$(mktemp)
            wget -O "$LLVM_SCRIPT" https://apt.llvm.org/llvm.sh
            chmod +x "$LLVM_SCRIPT"
            sudo "$LLVM_SCRIPT" 21 clang
            rm -f "$LLVM_SCRIPT"
            echo -e "${GREEN}✓ LLVM/Clang 21 installed${NC}"
        else
            echo -e "${GREEN}✓ LLVM/Clang 21 already installed${NC}"
        fi
    else
        echo -e "${YELLOW}Warning: apt-get not found. Please install dependencies manually.${NC}"
        echo "Required: build-essential cmake openssl libssl-dev zlib1g-dev gperf wget git curl libreadline-dev ccache libmicrohttpd-dev pkg-config libsodium-dev libsecp256k1-dev liblz4-dev ninja-build"
        echo "Also install LLVM/Clang 21: wget https://apt.llvm.org/llvm.sh && chmod +x llvm.sh && sudo ./llvm.sh 21 clang"
    fi
    
    # Clone repository with submodules
    TEMP_CPP_DIR=$(mktemp -d)
    echo "Cloning ton-blockchain/ton to $TEMP_CPP_DIR..."
    git clone --recurse-submodules https://github.com/ton-blockchain/ton.git "$TEMP_CPP_DIR"
    
    # Build TON
    echo "Building TON (this may take 10-30 minutes)..."
    cd "$TEMP_CPP_DIR"
    mkdir -p build
    cd build
    
    # Use clang-21 if available (required for TON)
    if command -v clang-21 &> /dev/null; then
        export CC=clang-21
        export CXX=clang++-21
        echo "Using clang-21 for compilation"
    fi
    
    cmake -DCMAKE_BUILD_TYPE=Release -GNinja ..
    cmake --build . --target emulator -j$(nproc)
    
    # Find and copy emulator library
    EMULATOR_LIB=""
    if [ -f "emulator/libemulator.so" ]; then
        EMULATOR_LIB="emulator/libemulator.so"
    elif [ -f "libemulator.so" ]; then
        EMULATOR_LIB="libemulator.so"
    else
        # Search for it
        EMULATOR_LIB=$(find . -name "libemulator.so" | head -n 1)
    fi
    
    if [ -n "$EMULATOR_LIB" ] && [ -f "$EMULATOR_LIB" ]; then
        cp "$EMULATOR_LIB" "$SCRIPT_DIR/cpp/libemulator.so"
        echo -e "${GREEN}✓ C++ emulator built and copied to cpp/libemulator.so${NC}"
    else
        echo -e "${RED}ERROR: Failed to build C++ emulator (libemulator.so not found)${NC}"
        echo "Searched in $TEMP_CPP_DIR/build/"
        exit 1
    fi
    
    # Cleanup
    cd "$SCRIPT_DIR"
    rm -rf "$TEMP_CPP_DIR"
    echo "Cleaned up temporary build directory"
fi

echo ""

# ==============================================================================
# Step 3: Setup Python Environment
# ==============================================================================
echo -e "${YELLOW}[3/3] Setting up Python environment...${NC}"

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}ERROR: python3 is not installed!${NC}"
    echo "Install with: sudo apt-get install python3 python3-pip python3-venv"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | awk '{print $2}')
echo "Found Python version: $PYTHON_VERSION"

# Check if venv module is available
if ! python3 -m venv --help &> /dev/null; then
    echo -e "${RED}ERROR: python3-venv is not installed!${NC}"
    echo "Install with: sudo apt-get install python3-venv"
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "my_venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv my_venv
    echo -e "${GREEN}✓ Virtual environment created${NC}"
else
    echo -e "${GREEN}✓ Virtual environment already exists${NC}"
fi

# Activate virtual environment
echo "Activating virtual environment..."
source my_venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install requirements
if [ -f "requirements.txt" ]; then
    echo "Installing Python dependencies from requirements.txt..."
    pip install -r requirements.txt
    echo -e "${GREEN}✓ Python dependencies installed${NC}"
else
    echo -e "${YELLOW}Warning: requirements.txt not found${NC}"
fi

# Install package in development mode
if [ -f "setup.py" ]; then
    echo "Installing TonTVMReplay package..."
    pip install -e .
    echo -e "${GREEN}✓ TonTVMReplay package installed${NC}"
else
    echo -e "${YELLOW}Warning: setup.py not found${NC}"
fi

echo ""
echo "================================================"
echo -e "${GREEN}Setup completed successfully!${NC}"
echo "================================================"
echo ""
echo "Next steps:"
echo "1. Activate the virtual environment:"
echo "   source my_venv/bin/activate"
echo ""
echo "2. Configure environment variables in .env file"
echo ""
echo "3. Run the tool:"
echo "   source .env && tonemuso"
echo ""
echo -e "${YELLOW}Note: The virtual environment is currently activated in this shell.${NC}"
