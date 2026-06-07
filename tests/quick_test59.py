# -*- coding: utf-8 -*-
import json, sys, io, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
url = 'http://localhost:8000/api/chat/stream'
payload = json.dumps({'message': '推荐手机，直接帮我加到购物车', 'session_id': 'test59fix', 'attachments': [], 'images': []}).encode('utf-8')
req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=60) as resp:
    buf = ''
    for raw in resp:
        buf += raw.decode('utf-8', errors='replace')
        while '\n\n' in buf:
            evt, buf = buf.split('\n\n', 1)
            etype = ''
            dlines = []
            for ln in evt.strip().split('\n'):
                if ln.startswith('event:'): etype = ln[6:].strip()
                elif ln.startswith('data:'): dlines.append(ln[5:].strip())
            if etype == 'tool_call' and dlines:
                d = json.loads(''.join(dlines))
                name = d.get('name')
                src = d.get('source')
                conf = d.get('confidence')
                print('Tool: %s, src: %s, conf: %s' % (name, src, conf))
            if etype == 'product_cards' and dlines:
                d = json.loads(''.join(dlines))
                prods = d.get('products', [])
                ids = [p.get('product_id') for p in prods]
                print('Cards: %d -> %s' % (len(ids), ids))
