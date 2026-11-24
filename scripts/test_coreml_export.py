import types
import unittest

import torch
from transformers import BertConfig, BertForTokenClassification


import scripts.export_coreml as exporter


class AttentionExportTests(unittest.TestCase):
    def test_finite_masks_preserve_logits_and_trace_dynamic_shapes(self):
        torch.manual_seed(42)
        config = BertConfig(vocab_size=32, hidden_size=16, num_hidden_layers=1,
                            num_attention_heads=2, intermediate_size=32,
                            num_labels=2, attn_implementation='eager')
        model = BertForTokenClassification(config).eval()
        examples = []
        with torch.no_grad():
            for batch, width in [(1, 16), (2, 7), (1, 1)]:
                features = torch.zeros((3, batch, width), dtype=torch.int32)
                features[0] = torch.arange(width) % config.vocab_size
                features[1, :, :max(1, width // 2)] = 1
                expected = model(input_ids=features[0], attention_mask=features[1],
                                 token_type_ids=features[2]).logits
                examples.append((features, expected))
            model.bert._create_attention_masks = types.MethodType(
                exporter.finite_attention_masks, model.bert)
            classifier = exporter.Classifier(model)
            traced = torch.jit.trace(classifier, examples[0][0])
            for features, expected in examples:
                with self.subTest(shape=tuple(features.shape)):
                    torch.testing.assert_close(classifier(features), expected, atol=1e-6, rtol=1e-5)
                    torch.testing.assert_close(traced(features), expected, atol=1e-6, rtol=1e-5)

    def test_decoder_attention_is_rejected(self):
        model = types.SimpleNamespace(config=types.SimpleNamespace(is_decoder=True))
        with self.assertRaisesRegex(ValueError, 'unsupported_attention_configuration'):
            exporter.finite_attention_masks(model, None, None, None, None, None)


if __name__ == '__main__':
    unittest.main()
