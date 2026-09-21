"""Export a self-contained design preview from a read-only demo snapshot.

Run buildcrmui.py first to refresh the embedded fonts and review UI.
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from review_service import read_state


def export_cards():
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = Path(temporary) / 'design-snapshot.sqlite'
        with sqlite3.connect((ROOT / 'data/demo.sqlite').as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(snapshot) as destination:
                source.backup(destination)
        data = read_state(snapshot)
    data = {key: data[key] for key in ('proposals', 'accounts', 'contacts')}
    data['exported_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    data['mode'] = 'test'
    embedded = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')
    embedded = embedded.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    frame = (ROOT / 'mistral-replica/dist/crm-content.html').read_text()
    header = ('<header class="export-header"><h1>Bellhaven — card design handoff</h1>'
              f'<p>Database snapshot · {data["exported_at"]} · {len(data["proposals"])} proposals.</p>'
              '<p id="export-status" role="status">Offline design preview. Expand cards and use filters to inspect them. '
              'Saving decisions is unavailable.</p></header>')
    bootstrap = ('<script type="application/json" id="bellhaven-data">' + embedded + '</script>'
                 '<script>window.Bellhaven={'
                 'data:JSON.parse(document.getElementById("bellhaven-data").textContent),'
                 'mutate:async()=>{throw new Error("Design preview: use the connected app to save decisions.");},'
                 'error:message=>document.getElementById("export-status").textContent=message};</script>')
    frame = frame.replace('<body>', '<body>' + header + bootstrap, 1)
    frame = frame.replace('</head>', '<style>body{background:#faf9f6;padding:32px;max-width:1600px;margin:auto}'
                          '.export-header{margin-bottom:32px}.export-header h1{font-size:24px;margin:0 0 10px}'
                          '.export-header p{color:#706b62;line-height:1.5;margin:6px 0}'
                          '@media(max-width:700px){body{padding:16px}}</style></head>', 1)
    frame = frame.replace('<title>Bellhaven CRM review</title>', '<title>Bellhaven — Cards with Real Data</title>')
    output = ROOT / 'exports/Bellhaven Cards - Real Data.html'
    output.parent.mkdir(exist_ok=True)
    output.write_text(frame)
    print(output)


if __name__ == '__main__':
    export_cards()
