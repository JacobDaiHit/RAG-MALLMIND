"""Fix the encoding of the system prompt in tool_router.py."""
import re

path = "rag/recommendation/tool_router.py"

with open(path, "rb") as f:
    data = f.read()

# The file is UTF-8 but the Edit tool wrote GBK bytes for the Chinese prompt.
# We need to find the corrupted block and replace it with correct UTF-8.

# First, try to decode the whole file as UTF-8 to find the broken area
text = data.decode("utf-8", errors="replace")

# Find the system prompt block and replace it
old_block_start = '"content": ('
old_block_end = "),"

# We need to find the exact block. Let's search for the pattern.
# The system prompt is inside try_llm_route_tool_call, in the chat_json_with_report call.
# Let's find it by looking for "role": "system" near the routing code.

# Strategy: find the system prompt block by its context
lines = text.split("\n")
start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if '"role": "system"' in line and start_idx is None:
        # Check if this is the one inside try_llm_route_tool_call
        # by looking at surrounding context
        if i > 890 and i < 920:
            start_idx = i
    if start_idx is not None and i > start_idx and ")," in line.strip() and end_idx is None:
        end_idx = i

print(f"Found system prompt block: lines {start_idx+1} to {end_idx+1}")

if start_idx is not None and end_idx is not None:
    # Build the replacement block
    indent = "                            "
    new_lines = [
        '                    {',
        '                        "role": "system",',
        '                        "content": (',
        '                            "\\u4f60\\u662f\\u7535\\u5546\\u5bfc\\u8d2d\\u7cfb\\u7edf\\u7684\\u5de5\\u5177\\u8def\\u7531\\u5668\\uff0c\\u53ea\\u8f93\\u51fa JSON\\u3002"',
        '                            "\\u540e\\u7aef\\u4f1a\\u6821\\u9a8c\\u5e76\\u6267\\u884c\\u5de5\\u5177\\uff0c\\u4f60\\u53ea\\u8d1f\\u8d23\\u9009\\u62e9\\u5de5\\u5177\\u548c\\u62bd\\u53d6\\u53c2\\u6570\\u3002"',
        '                            "\\u4e0d\\u8981\\u7f16\\u9020\\u5546\\u54c1\\u3001\\u4ef7\\u683c\\u3001\\u5e93\\u5b58\\u6216\\u4f18\\u60e0\\u3002\\n"',
        '                            "\\u8def\\u7531\\u539f\\u5219\\uff1a\\n"',
        '                            "- \\u53ea\\u8981\\u7528\\u6237\\u5728\\u8be2\\u95ee\\u3001\\u5bfb\\u627e\\u3001\\u8bc4\\u4ef7\\u4efb\\u4f55\\u5546\\u54c1\\uff08\\u5305\\u62ec\\u836f\\u54c1\\u3001\\u4fdd\\u5065\\u54c1\\u7b49\\u975e\\u5178\\u578b\\u54c1\\u7c7b\\uff09\\uff0c\\u4e00\\u5f8b\\u4f7f\\u7528 recommend_shopping_products\\u3002\\n"',
        '                            "- \\u7528\\u6237\\u8981\\u6c42\\u201c\\u914d\\u4e00\\u5957\\u201d\\u201c\\u4e00\\u8d77\\u63a8\\u8350\\u201d\\u201c\\u5f00\\u5b66\\u8981\\u7528\\u7684\\u4e1c\\u897f\\u201d\\u7b49\\u591a\\u5546\\u54c1\\u6216\\u573a\\u666f\\u5316\\u8bf7\\u6c42\\uff0c\\u4f7f\\u7528 recommend_shopping_products\\u3002\\n"',
        '                            "- \\u201c\\u54ea\\u4e2a\\u66f4\\u4e0d\\u6cb9\\u201d\\u201c\\u54ea\\u4e2a\\u66f4\\u8f7b\\u201d\\u201c\\u54ea\\u6b3e\\u66f4\\u9002\\u5408\\u201d\\u7b49\\u8868\\u8fbe\\u662f\\u5c5e\\u6027\\u504f\\u597d\\u7b5b\\u9009\\uff0c\\u4e0d\\u662f\\u5546\\u54c1\\u5bf9\\u6bd4\\uff0c\\u4f7f\\u7528 recommend_shopping_products\\u3002\\n"',
        '                            "- \\u53ea\\u6709\\u7528\\u6237\\u660e\\u786e\\u63d0\\u5230\\u4e24\\u4e2a\\u5177\\u4f53\\u5546\\u54c1\\u540d\\u5e76\\u8981\\u6c42\\u6bd4\\u8f83\\u65f6\\u624d\\u4f7f\\u7528 compare_products\\u3002\\n"',
        '                            \'- general_chat \\u4ec5\\u7528\\u4e8e\\u201c\\u4f60\\u662f\\u8c01\\u201d\\u201c\\u600e\\u4e48\\u7528\\u201d\\u201c\\u63a8\\u8350\\u903b\\u8f91\\u662f\\u4ec0\\u4e48\\u201d\\u7b49\\u7cfb\\u7edf\\u5143\\u95ee\\u9898\\u3002\\n\'',
        '                            \'- \\u8f93\\u51fa JSON \\u4e2d\\u5fc5\\u987b\\u5305\\u542b "source": "llm"\\u3002\'',
        '                        ),',
        '                    },',
    ]
    
    # Now we need to find the exact range to replace.
    # The block starts at the '{' before "role": "system" and ends at the '},' after the content
    # Let's find the '{' line before start_idx
    brace_start = start_idx - 1  # The line with just '{'
    brace_end = end_idx + 1  # The line with '},'
    
    print(f"Replacing lines {brace_start+1} to {brace_end+1}")
    print(f"Old content preview: {lines[brace_start][:60]}")
    
    new_lines_joined = [line for line in new_lines]
    
    # Replace in the lines list
    lines[brace_start:brace_end+1] = new_lines_joined
    
    # Write back
    new_text = "\n".join(lines)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_text)
    
    print("File written successfully with UTF-8 encoding.")
else:
    print("Could not find the system prompt block to fix.")
