"""StanThrust: preliminary liquid rocket engine sizing."""

__version__ = "0.2.0"

#: Runtimes the pinned wheels and CI are tested against.
SUPPORTED_PYTHON = "3.11-3.13"


def dependency_error_message(package: str, error: BaseException) -> str:
    """Explain an import failure, separating "not installed" from "will not load".

    A compiled extension that is present but refuses to load needs different
    advice from a package that was never installed. On Windows the usual cause
    is a security policy such as Smart App Control blocking an unsigned wheel,
    which reinstalling does not fix.
    """
    if isinstance(error, ModuleNotFoundError):
        return (
            f"{package} is required for StanThrust. Install the project "
            "dependencies with 'python -m pip install -r requirements.txt'."
        )
    return (
        f"{package} is installed but its compiled extension failed to load "
        f"({error}). That usually means the wheel does not match the running "
        f"Python version, or a system security policy is blocking it. Python "
        f"{SUPPORTED_PYTHON} is the supported runtime; on Windows, wheels built "
        "for a very new Python release can also be blocked by Smart App Control."
    )
