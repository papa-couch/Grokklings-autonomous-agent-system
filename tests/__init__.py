import logging

# Colony alerts are expected during tests — keep them out of the output.
logging.getLogger("grokklings").setLevel(logging.CRITICAL)
