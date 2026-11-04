"""Keep tests from reading or writing the real per-machine usage host-id file."""
import os
import tempfile

os.environ.setdefault(
    "CC_USAGE_HOST_FILE",
    os.path.join(tempfile.mkdtemp(prefix="cc-usage-host-"), "host-id"))
