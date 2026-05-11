import sys
import os

# Add src/ to path so all backend modules resolve without package prefix
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
