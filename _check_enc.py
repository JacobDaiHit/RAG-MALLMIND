"""Check encoding of tool_router.py around the system prompt area."""
import sys

with open('rag/recommendation/tool_router.py', 'rb') as f:
    data = f.read()

lines = data.split(b'\x0a')
print(f"Total lines: {len(lines)}")

for i in range(910, min(925, len(lines))):
    raw = lines[i]
    try:
        decoded = raw.decode('utf-8')
        status = 'UTF8'
    except UnicodeDecodeError:
        try:
            decoded = raw.decode('gbk')
            status = 'GBK'
        except UnicodeDecodeError:
            decoded = raw.decode('latin-1')
            status = 'LATIN1'
    print(f"L{i+1} [{status}]: {decoded[:120]}")
