#!/bin/sh
#
# Sets up git hooks for this repository.
# Run once after cloning: ./setup-hooks.sh
#
# This configures git to use .githooks/ as the hooks directory,
# so hooks are version-controlled and shared with anyone who clones the repo.

set -e

echo "🔧 Configuring git hooks..."

# Point git to the .githooks directory
git config core.hooksPath .githooks

# Ensure hooks are executable
chmod +x .githooks/*

echo "✓ Git hooks installed"
echo ""
echo "Hooks enabled:"
echo "  pre-commit  — secrets detection + large file check"
echo "  commit-msg  — Conventional Commits validation"
echo "  pre-push    — branch naming validation"
echo ""
echo "Skip any hook with --no-verify flag"
echo "Configure max file size: git config hooks.maxfilesize <bytes>"
