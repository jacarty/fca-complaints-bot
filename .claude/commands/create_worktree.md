Your task is to create a new worktree named $ARGUMENTS in the .trees/$ARGUMENTS folder.

Follow these steps:

1. Check if an existing folder in the .trees folder with the name $ARGUMENTS already exists. If it does, stop here and tell the user the worktree already exists.

2. Create a new git worktree in the .trees folder with the name $ARGUMENTS.

3. If a `.venv` directory exists in the project root, symlink it into the worktree directory so the virtualenv is shared. If `.venv` does not exist, skip this step silently.

4. If the `code` command is available on PATH, launch a new VS Code editor instance in the worktree directory. If `code` is not available, tell the user the worktree is ready and print the path so they can open it in their preferred editor.
