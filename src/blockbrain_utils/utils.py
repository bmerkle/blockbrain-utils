"""Example utility functions for demonstration purposes."""


def greet(name: str) -> str:
    """
    Generate a greeting message.

    Args:
        name: The name to greet.

    Returns:
        A greeting message string.

    Example:
        >>> greet("World")
        'Hello, World!'
    """
    return f"Hello, {name}!"


def add(a: float, b: float) -> float:
    """
    Add two numbers together.

    Args:
        a: First number.
        b: Second number.

    Returns:
        The sum of a and b.

    Example:
        >>> add(2, 3)
        5
    """
    return a + b
