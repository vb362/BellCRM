"""Compose the supplied review content with the original Mistral shell, offline."""
from pathlib import Path
import base64
import gzip
import hashlib
import html
import json
import re

ROOT = Path(__file__).resolve().parent
DIST = ROOT / 'dist'
REF = ROOT / 'reference'
template = (REF / 'bellhaven-template.html').read_text()
manifest = json.loads((REF / 'bellhaven-manifest.json').read_text())

def payload(key):
    entry = manifest[key]
    data = base64.b64decode(entry['data'])
    return gzip.decompress(data) if entry.get('compressed') else data

def inline_asset(key):
    return 'data:' + manifest[key]['mime'] + ';base64,' + base64.b64encode(payload(key)).decode()

fonts = re.search(r'<helmet>.*?(<style>.*?</style>)', template, re.S)[1]
for key, entry in manifest.items():
    if entry['mime'].startswith('font/'):
        fonts = fonts.replace(key, inline_asset(key))
# Reuse the original Inter font for the reference's row typography.
inter_faces = '\n'.join(re.findall(r'@font-face\{[^}]*font-family:Inter;[^}]*\}', (DIST / 'assets/0z0p1xalsq-g1.css').read_text()))
inter_faces = re.sub(r'url\(\.\./media/([^)]*)\)', lambda m: 'url(data:font/woff2;base64,' + base64.b64encode((DIST / 'media' / m[1]).read_bytes()).decode() + ')', inter_faces)
fonts += '<style>' + inter_faces + '</style>'
bridge = '''
(() => {
  let frame;
  let lastHeight = 0;
  const report = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const root = document.getElementById('crm-review-content');
      if (!root) return;
      const height = Math.ceil(root.getBoundingClientRect().height);
      if (height !== lastHeight) {
        lastHeight = height;
        parent.postMessage({type:'crm-content-height', height}, '*');
      }
    });
  };
  document.addEventListener('DOMContentLoaded', () => {
    new ResizeObserver(report).observe(document.body);
    document.fonts.ready.then(report);
  });
})();
'''
# The review prototype uses the original fonts and a self-contained UI module.
frame = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<title>Bellhaven CRM review</title>' + fonts
    + '<style data-review-styles>' + (DIST / 'assets/review.css').read_text() + '</style>'
    + '<script>' + bridge + '</script></head><body>'
    + (ROOT / 'review.html').read_text()
    + '<script>' + (DIST / 'assets/review.js').read_text().replace('</script', '<\\/script')
    + '</script></body></html>')
(DIST / 'crm-content.html').write_text(frame)

shell = (REF / 'shell.html').read_text()
shell = shell.replace('<title>AI Studio - Mistral AI</title>', '<title>CRM — Bellhaven Review</title>')
# Restore the original expanded app label alongside the existing artwork.
shell = re.sub(r'<button\b[^>]*aria-label="Open app switcher \(Studio\)"[^>]*>.*?</button>', lambda _: (REF / 'app-switcher.html').read_text(), shell, count=1, flags=re.S)
# Replace only the artwork inside the existing 24px top-left icon container.
brand_icon = 'data:image/png;base64,' + base64.b64encode((DIST / 'assets/brand-icon.png').read_bytes()).decode()
# Reuse the exact clipboard artwork for the tab icon in both HTML outputs.
shell = shell.replace('<link rel="icon" href="assets/favicon.svg" type="image/svg+xml">',
                      '<link rel="icon" href="' + brand_icon + '" type="image/png">')
shell, logo_replacements = re.subn(
    r'(<div class="flex size-6 items-center justify-center overflow-hidden rounded-\[4px\]" style="background-color:#0082E6">)<div class="flex h-4 w-4 items-center justify-center"><svg\b.*?</svg></div>',
    lambda match: match[1] + '<img src="' + brand_icon + '" alt="" width="24" height="24" style="display:block;width:24px;height:24px;object-fit:contain">',
    shell, count=1, flags=re.S,
)
assert logo_replacements == 1, 'Expected one top-left app icon'
# Keep the original row styling, using the supplied review's semantic icons.
rows = re.findall(r'<li data-sidebar="menu-item"[^>]*>.*?</li>', shell, re.S)
home_row = rows[0]
icons = []
for key in ['home', 'runs', 'sources', 'settings']:
    button = re.search(r'<button sc-camel-on-click="\{\{ n\.' + key + r'\.go \}\}".*?</button>', template, re.S)[0]
    icon = re.search(r'<svg\b.*?</svg>', button, re.S)[0]
    icon = icon.replace('sc-camel-view-box=', 'viewBox=').replace('stroke-width="1.7"', 'stroke-width="2"')
    icons.append(icon)
new_rows = []
welcome_icon = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M14 9l3 3-3 3"/></svg>'
report_icon = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 3H5v18h14V8l-5-5Z"/><path d="M14 3v5h5M8 12h8M8 16h5"/></svg>'
for index, (label, icon) in enumerate(zip(['Welcome', 'Home', 'Runs', 'Decisions', 'Sources', 'Settings'], [welcome_icon, icons[0], icons[1], report_icon, icons[2], icons[3]])):
    row = home_row.replace('>Accueil<', '>' + label + '<')
    row = re.sub(r'<svg\b.*?</svg>', lambda _: icon, row, count=1, flags=re.S)
    row = row.replace('href="https://console.mistral.ai/home"', 'href="#' + label.lower() + '"')
    if index:
        row = row.replace('data-active="true"', 'data-active="false"')
    new_rows.append(row)
menu_start = shell.index('<ul data-sidebar="menu"')
menu_open_end = shell.index('>', menu_start) + 1
depth = 0
for match in re.finditer(r'</?ul\b[^>]*>', shell[menu_start:]):
    depth += -1 if match[0].startswith('</') else 1
    if depth == 0:
        menu_end = menu_start + match.end()
        break
menu = shell[menu_start:menu_open_end] + '<div data-sidebar="group" class="relative flex w-full min-w-0 flex-col p-0"><div data-sidebar="group-content" class="w-full text-sm">' + ''.join(new_rows) + '</div></div></ul>'
shell = shell[:menu_start] + menu + shell[menu_end:]
start = shell.index('<main')
end = shell.index('</main>', start)
main_shell = shell[start:end]
# Preserve the original title and subtitle typography and spacing.
header = '''<header class="flex flex-col w-full shrink-0 pb-6 text-default"><div class="relative"><h1 id="crm-page-title" tabindex="-1" class="transition-colors [p,div]:whitespace-pre-line text-2xl leading-[2rem] font-medium font-headings">Welcome</h1><p class="group/text-truncator relative grid text-muted min-w-0 overflow-hidden text-sm leading-[1.25rem] mt-1"><span id="crm-page-description" class="line-clamp-2">Choose how you want to work with Bellhaven.</span></p><div aria-hidden="true" class="absolute inset-x-0 bottom-0 h-px"></div></div></header>'''
header = header.replace('<div class="relative"><h1', '<div class="relative crm-heading-row"><div><h1')
header = header.replace('<div aria-hidden="true"', '</div><button id="runs-new-button" type="button" class="runs-primary" hidden><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 12h18M12 3v18"/></svg>New run</button><div aria-hidden="true"')
main_shell = re.sub(r'<header\b[^>]*>.*?</header>', lambda _: header, main_shell, count=1, flags=re.S)
# The sticky top bar keeps the same height and transition as the source.
sticky_content = '<div class="flex justify-start items-center gap-4 app-shell-top-bar-6 w-full"><span id="crm-sticky-title" class="transition-colors [p,div]:whitespace-pre-line text-sm leading-[1.25rem] font-medium truncate">Welcome</span></div>'
def replace_element(source, marker, replacement):
    begin = source.index(marker)
    depth = 0
    for match in re.finditer(r'</?div\b[^>]*>', source[begin:]):
        depth += -1 if match[0].startswith('</') else 1
        if depth == 0:
            return source[:begin] + replacement + source[begin+match.end():]
    raise ValueError('Unbalanced element: ' + marker)
main_shell = replace_element(main_shell, '<div class="flex justify-start items-center gap-4 app-shell-top-bar-6 w-full">', sticky_content)
# Embed the same robot artwork in both cards, including the standalone output.
robot_icon = 'data:image/svg+xml;base64,' + base64.b64encode((DIST / 'media/robotic-icon.2ia82qs02dth5.svg').read_bytes()).decode()
welcome = (ROOT / 'welcome.html').read_text().replace('{{ robot_icon }}', robot_icon)
content = welcome + (ROOT / 'runs.html').read_text() + (ROOT / 'sources.html').read_text() + (ROOT / 'settings.html').read_text() + '<div id="crm-review-panel" hidden class="min-h-0 w-full grow shrink-0 pb-6" style="position:relative"><iframe id="crm-content" title="Bellhaven CRM review" style="display:block;width:100%;height:900px;border:0;background:transparent" srcdoc="' + html.escape(frame, quote=True) + '"></iframe></div>'
main_shell = replace_element(main_shell, '<div class="min-h-0 w-full grow shrink-0 pb-6">', content)
shell = shell[:start] + main_shell + shell[end:]
shell = replace_element(shell, '<div data-sidebar="footer"', (REF / 'sidebar-footer.html').read_text())
shell = shell.replace('</head>', '<style>' + (DIST / 'assets/sidebar-footer.css').read_text() + '</style></head>')
shell = shell.replace('</head>', '<style>' + (DIST / 'assets/welcome.css').read_text() + '</style></head>')
shell = shell.replace('</head>', '<style>' + (DIST / 'assets/runs.css').read_text() + '</style></head>')
shell = shell.replace('</head>', '<style>' + (DIST / 'assets/sources.css').read_text() + '</style></head>')
shell = shell.replace('</head>', '<style>' + (DIST / 'assets/settings.css').read_text() + '</style></head>')
shell = shell.replace('</body>', '<script>' + (DIST / 'assets/app.js').read_text() + '</script></body>')
# Runs logic is bundled into both outputs so the single HTML stays offline.
shell = shell.replace('</body>', '<script>' + (DIST / 'assets/runs.js').read_text() + '</script></body>')
# A changed script gets a changed URL so refreshing cannot reuse old UI logic.
script_version = hashlib.sha256((DIST / 'assets/replica.js').read_bytes()).hexdigest()[:12]
shell = shell.replace('src="assets/replica.js"', f'src="assets/replica.js?v={script_version}"')
(DIST / 'index.html').write_text(shell)

# Produce the same single-file deliverable, still usable through file://.
def data(path, mime):
    return 'data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode()
def embed_styles(match):
    css = (DIST / match[1]).read_text()
    css = re.sub(r'url\(\.\./media/([^)]*)\)', lambda m: 'url(' + data(DIST / 'media' / m[1], 'font/woff2') + ')', css)
    return '<style>' + css + '</style>'
standalone = re.sub(r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"[^>]*>', embed_styles, shell)
standalone = standalone.replace(f'<script src="assets/replica.js?v={script_version}" defer></script>', '')
standalone = standalone.replace('</body>', '<script>' + (DIST / 'assets/replica.js').read_text() + '</script></body>')
(ROOT / 'Mistral Studio Replica.html').write_text(standalone)
print('Updated source and self-contained CRM page.')
