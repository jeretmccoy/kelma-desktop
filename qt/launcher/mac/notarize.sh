#!/bin/bash

set -e

# Define output path
OUTPUT_DIR="$1"
APP_LAUNCHER="$OUTPUT_DIR/Kelma.app"
ZIP_FILE="$OUTPUT_DIR/Kelma.zip"

# Create zip for notarization
(cd "$OUTPUT_DIR" && rm -rf Kelma.zip && zip -r Kelma.zip Kelma.app)

# Upload for notarization
xcrun notarytool submit "$ZIP_FILE" -p default --wait

# Staple the app
xcrun stapler staple "$APP_LAUNCHER"