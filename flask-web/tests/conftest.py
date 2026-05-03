import sys
import os

# Allow `from helpers import ...` in test files.
sys.path.insert(0, os.path.dirname(__file__))
# Allow `import db`, `import config`, etc. from the flask-web package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
