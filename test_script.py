# test_script.py
import sys

print("Hello from inside Docker!")
print(f"Python version: {sys.version}")

# Example of basic arithmetic
a = 10
b = 5
print(f"Sum: {a + b}")

# Example of command-line arguments (if you pass them)
if len(sys.argv) > 1:
    print(f"Argument received: {sys.argv[1]}")