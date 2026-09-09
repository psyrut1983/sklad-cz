"""The shared shell preserves existing controller targets on both entry URLs."""
from collections import Counter
from html.parser import HTMLParser
import re
from pathlib import Path

from app.chestny.factory import create_cz_app


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.panels = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if 'data-panel' in attrs:
            self.panels.append(attrs['data-panel'])


def test_both_entry_urls_keep_controller_targets(tmp_path):
    app = create_cz_app(instance_path=str(tmp_path), testing=True)
    client = app.test_client()
    for url in ('/', '/turnover'):
        response = client.get(url)
        assert response.status_code == 200
        elements = Elements()
        elements.feed(response.get_data(as_text=True))
        assert all(count == 1 for count in Counter(elements.ids).values())
        assert elements.panels == ['work', 'history', 'settings']
        for filename in ('settings.js', 'turnover.js'):
            script = (Path(app.static_folder) / 'chestny' / filename).read_text(encoding='utf-8')
            targets = re.findall(r'getElementById\("([\w-]+)"\)', script)
            if filename == 'turnover.js':
                targets += re.findall(r"\$\('([\w-]+)'\)", script)
            assert set(targets) <= set(elements.ids)
