"""Regression cases for imported signer helpers and their eager dependencies."""
import unittest

from source_graph import Graph, MemorySnapshot


def closure(files, name, path='test_sign.py'):
    graph = Graph({'new': MemorySnapshot(files)})
    root = next(n for n in graph.module('new', path) if name in graph.nodes[n].names)
    reached, issues = graph.closure([root])
    return '\n'.join(graph.nodes[n].text for n in reached), issues


class PythonEvidenceTests(unittest.TestCase):
    def test_python_precondition_is_not_a_loader(self):
        text, issues = closure({'test_sign.py': 'def require(ok, code): assert ok, code\n'
                                'def sign(ok): require(ok, "bad")\n'}, 'sign')
        self.assertFalse(issues)
        self.assertIn('assert ok, code', text)
        self.assertTrue(closure({'test_sign.py': 'def sign(name): return __import__(name)\n'}, 'sign')[1])

    def test_resolves_constructor_methods_and_preserves_escaped_classes(self):
        files = {'helper.py': 'class Thing:\n def accept(self): return 1234\n def unknown(self): return eval(value)\n',
                 'test_sign.py': 'import helper as h\ndef test_it():\n x = h.Thing()\n return x.accept()\n'}
        text, issues = closure(files, 'test_it')
        self.assertFalse(issues)
        self.assertIn('return 1234', text)
        for source in ['x = h.Thing()\n return other(x)', 'return other(h.Thing)', 'return other(h)']:
            files['test_sign.py'] = 'import helper as h\ndef test_it():\n '+source+'\n'
            self.assertTrue(closure(files, 'test_it')[1], source)

    def test_no_space_main_guard_and_eager_decorator_arguments(self):
        files = {'helper.py': 'import unittest\n@unittest.skipUnless(enabled(), "fixture")\nclass Fixture:\n'
                 ' def unused(self): return eval(value)\ndef enabled(): return 1234\n'
                 'def run(): return 23\nif __name__=="__main__": eval(value)\n',
                 'test_sign.py': 'import helper as h\ndef f(): return h.run()\n'}
        text, issues = closure(files, 'f')
        self.assertFalse(issues)
        self.assertIn('return 1234', text)
        self.assertIn('return 23', text)
        files['helper.py'] += 'else: eval(value)\n'
        self.assertTrue(closure(files, 'f')[1])

    def test_shadowed_and_unknown_class_decorators_keep_bodies(self):
        for decorator in ['@decorate', '@unittest.skipUnless(True, "fixture")']:
            files = {'test_sign.py': 'import unittest\nunittest = custom\n'+decorator+
                     '\nclass Target:\n def run(self): return eval(name)\ndef f(): return Target()\n'}
            self.assertTrue(closure(files, 'f')[1])
