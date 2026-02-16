"""Tests for the utils module."""

import pytest
from blockbrain_utils.utils import greet, add


def test_greet():
    """Test the greet function."""
    assert greet("World") == "Hello, World!"
    assert greet("Python") == "Hello, Python!"


def test_greet_empty_string():
    """Test greet with an empty string."""
    assert greet("") == "Hello, !"


def test_add():
    """Test the add function."""
    assert add(2, 3) == 5
    assert add(-1, 1) == 0
    assert add(0, 0) == 0


def test_add_floats():
    """Test add with floating point numbers."""
    assert add(1.5, 2.5) == 4.0
    assert add(0.1, 0.2) == pytest.approx(0.3)
