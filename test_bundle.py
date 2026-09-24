import json
import tempfile
import unittest
from pathlib import Path
from bundle_evidence import bundle
from loop import digest, save


class BundleTests(unittest.TestCase):
    def test_preserves_sources_and_binds_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = [Path(temp) / f'{n}.json' for n in range(2)]
            for n, path in enumerate(paths):
                save(path, {'source_id': '重复编号', 'url': f'https://example.com/{n}', 'content': f'证据{n}',
                            'content_sha256': digest(f'证据{n}'), 'published_at': None})
            first = bundle(paths)
            sources = json.loads(first['content'])
            self.assertEqual([s['source_id'] for s in sources], ['S1', 'S2'])
            self.assertEqual(sources[1]['content'], '证据1')
            changed = json.loads(paths[0].read_text())
            changed['url'] = 'https://example.com/changed'
            save(paths[0], changed)
            self.assertNotEqual(first['content_sha256'], bundle(paths)['content_sha256'])

    def test_rejects_failed_or_tampered_source(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'source.json'
            for content, error, sha in [('text', 'timeout', digest('text')),
                                        ('changed', None, digest('original')),
                                        ('', None, digest(''))]:
                save(path, {'url': 'https://example.com', 'content': content,
                            'error': error, 'content_sha256': sha})
                with self.assertRaises(ValueError):
                    bundle([path])


if __name__ == '__main__':
    unittest.main()
