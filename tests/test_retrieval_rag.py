import json
import os
import pickle
import shutil
import tempfile
from unittest import TestCase
from unittest.mock import patch

import numpy as np
from datasets import Dataset

import faiss
from transformers.configuration_bart import BartConfig
from transformers.configuration_dpr import DPRConfig
from transformers.configuration_rag import RagConfig
from transformers.retrieval_rag import RagRetriever
from transformers.testing_utils import require_datasets, require_faiss, require_torch
from transformers.tokenization_bart import BartTokenizer
from transformers.tokenization_bert import VOCAB_FILES_NAMES as DPR_VOCAB_FILES_NAMES
from transformers.tokenization_dpr import DPRQuestionEncoderTokenizer
from transformers.tokenization_roberta import VOCAB_FILES_NAMES as BART_VOCAB_FILES_NAMES


@require_faiss
@require_datasets
class RagRetrieverTest(TestCase):
    def setUp(self):
        self.tmpdirname = tempfile.mkdtemp()
        self.retrieval_vector_size = 8

        # DPR tok
        vocab_tokens = [
            "[UNK]",
            "[CLS]",
            "[SEP]",
            "[PAD]",
            "[MASK]",
            "want",
            "##want",
            "##ed",
            "wa",
            "un",
            "runn",
            "##ing",
            ",",
            "low",
            "lowest",
        ]
        dpr_tokenizer_path = os.path.join(self.tmpdirname, "dpr_tokenizer")
        os.makedirs(dpr_tokenizer_path, exist_ok=True)
        self.vocab_file = os.path.join(dpr_tokenizer_path, DPR_VOCAB_FILES_NAMES["vocab_file"])
        with open(self.vocab_file, "w", encoding="utf-8") as vocab_writer:
            vocab_writer.write("".join([x + "\n" for x in vocab_tokens]))

        # BART tok
        vocab = [
            "l",
            "o",
            "w",
            "e",
            "r",
            "s",
            "t",
            "i",
            "d",
            "n",
            "\u0120",
            "\u0120l",
            "\u0120n",
            "\u0120lo",
            "\u0120low",
            "er",
            "\u0120lowest",
            "\u0120newer",
            "\u0120wider",
            "<unk>",
        ]
        vocab_tokens = dict(zip(vocab, range(len(vocab))))
        merges = ["#version: 0.2", "\u0120 l", "\u0120l o", "\u0120lo w", "e r", ""]
        self.special_tokens_map = {"unk_token": "<unk>"}

        bart_tokenizer_path = os.path.join(self.tmpdirname, "bart_tokenizer")
        os.makedirs(bart_tokenizer_path, exist_ok=True)
        self.vocab_file = os.path.join(bart_tokenizer_path, BART_VOCAB_FILES_NAMES["vocab_file"])
        self.merges_file = os.path.join(bart_tokenizer_path, BART_VOCAB_FILES_NAMES["merges_file"])
        with open(self.vocab_file, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(vocab_tokens) + "\n")
        with open(self.merges_file, "w", encoding="utf-8") as fp:
            fp.write("\n".join(merges))

    def get_dpr_tokenizer(self) -> DPRQuestionEncoderTokenizer:
        return DPRQuestionEncoderTokenizer.from_pretrained(os.path.join(self.tmpdirname, "dpr_tokenizer"))

    def get_bart_tokenizer(self) -> BartTokenizer:
        return BartTokenizer.from_pretrained(os.path.join(self.tmpdirname, "bart_tokenizer"))

    def tearDown(self):
        shutil.rmtree(self.tmpdirname)

    def get_dummy_hf_index_retriever(self):
        dataset = Dataset.from_dict(
            {
                "id": ["0", "1"],
                "text": ["foo", "bar"],
                "title": ["Foo", "Bar"],
                "embeddings": [np.ones(self.retrieval_vector_size), 2 * np.ones(self.retrieval_vector_size)],
            }
        )
        dataset.add_faiss_index("embeddings", string_factory="Flat", metric_type=faiss.METRIC_INNER_PRODUCT)
        config = RagConfig(
            retrieval_vector_size=self.retrieval_vector_size,
            question_encoder=DPRConfig().to_dict(),
            generator=BartConfig().to_dict(),
        )
        with patch("transformers.retrieval_rag.load_dataset") as mock_load_dataset:
            mock_load_dataset.return_value = dataset
            retriever = RagRetriever(
                config,
                question_encoder_tokenizer=self.get_dpr_tokenizer(),
                generator_tokenizer=self.get_bart_tokenizer(),
            )
        return retriever

    def get_dummy_legacy_index_retriever(self):
        dataset = Dataset.from_dict(
            {
                "id": ["0", "1"],
                "text": ["foo", "bar"],
                "title": ["Foo", "Bar"],
                "embeddings": [np.ones(self.retrieval_vector_size + 1), 2 * np.ones(self.retrieval_vector_size + 1)],
            }
        )
        dataset.add_faiss_index("embeddings", string_factory="Flat", metric_type=faiss.METRIC_INNER_PRODUCT)

        index_file_name = os.path.join(self.tmpdirname, "hf_bert_base.hnswSQ8_correct_phi_128.c_index")
        dataset.save_faiss_index("embeddings", index_file_name + ".index.dpr")
        pickle.dump(dataset["id"], open(index_file_name + ".index_meta.dpr", "wb"))

        passages_file_name = os.path.join(self.tmpdirname, "psgs_w100.tsv.pkl")
        passages = {sample["id"]: [sample["text"], sample["title"]] for sample in dataset}
        pickle.dump(passages, open(passages_file_name, "wb"))

        config = RagConfig(
            retrieval_vector_size=self.retrieval_vector_size,
            question_encoder=DPRConfig().to_dict(),
            generator=BartConfig().to_dict(),
            index_name="legacy",
            index_path=self.tmpdirname,
            passages_path=self.tmpdirname,
        )
        retriever = RagRetriever(
            config, question_encoder_tokenizer=self.get_dpr_tokenizer(), generator_tokenizer=self.get_bart_tokenizer()
        )
        return retriever

    def test_hf_index_retriever_retrieve(self):
        n_docs = 1
        retriever = self.get_dummy_hf_index_retriever()
        hidden_states = np.array(
            [np.ones(self.retrieval_vector_size), -np.ones(self.retrieval_vector_size)], dtype=np.float32
        )
        retrieved_doc_embeds, doc_ids, doc_dicts = retriever.retrieve(hidden_states, n_docs=n_docs)
        self.assertEqual(retrieved_doc_embeds.shape, (2, n_docs, self.retrieval_vector_size))
        self.assertEqual(len(doc_dicts), 2)
        self.assertEqual(sorted(doc_dicts[0]), ["embeddings", "id", "text", "title"])
        self.assertEqual(len(doc_dicts[0]["id"]), n_docs)
        self.assertEqual(doc_dicts[0]["id"][0], "1")  # max inner product is reached with second doc
        self.assertEqual(doc_dicts[1]["id"][0], "0")  # max inner product is reached with first doc
        self.assertListEqual(list(doc_ids), [1, 0])

    def test_legacy_index_retriever_retrieve(self):
        n_docs = 1
        retriever = self.get_dummy_legacy_index_retriever()
        hidden_states = np.array(
            [np.ones(self.retrieval_vector_size), -np.ones(self.retrieval_vector_size)], dtype=np.float32
        )
        retrieved_doc_embeds, doc_ids, doc_dicts = retriever.retrieve(hidden_states, n_docs=n_docs)
        self.assertEqual(retrieved_doc_embeds.shape, (2, n_docs, self.retrieval_vector_size))
        self.assertEqual(len(doc_dicts), 2)
        self.assertEqual(sorted(doc_dicts[0]), ["text", "title"])
        self.assertEqual(len(doc_dicts[0]["text"]), n_docs)
        self.assertEqual(doc_dicts[0]["text"][0], "bar")  # max inner product is reached with second doc
        self.assertEqual(doc_dicts[1]["text"][0], "foo")  # max inner product is reached with first doc
        self.assertListEqual(list(doc_ids), [1, 0])

    @require_torch
    def test_hf_index_retriever_call(self):
        import torch

        n_docs = 1
        retriever = self.get_dummy_hf_index_retriever()
        question_input_ids = [[5, 7], [10, 11]]
        hidden_states = np.array(
            [np.ones(self.retrieval_vector_size), -np.ones(self.retrieval_vector_size)], dtype=np.float32
        )
        out = retriever(question_input_ids, hidden_states, prefix=retriever.config.generator.prefix, n_docs=n_docs)
        context_input_ids, context_attention_mask, retrieved_doc_embeds = (
            out["context_input_ids"],
            out["context_attention_mask"],
            out["retrieved_doc_embeds"],
        )
        self.assertEqual(retrieved_doc_embeds.shape, (2, n_docs, self.retrieval_vector_size))
        self.assertIsInstance(context_input_ids, list)
        self.assertIsInstance(context_attention_mask, list)
        self.assertIsInstance(retrieved_doc_embeds, np.ndarray)

        out = retriever(
            question_input_ids,
            hidden_states,
            prefix=retriever.config.generator.prefix,
            n_docs=n_docs,
            return_tensors="pt",
        )
        context_input_ids, context_attention_mask, retrieved_doc_embeds, doc_ids = (  # noqa: F841
            out["context_input_ids"],
            out["context_attention_mask"],
            out["retrieved_doc_embeds"],
            out["doc_ids"],
        )
        self.assertEqual(retrieved_doc_embeds.shape, (2, n_docs, self.retrieval_vector_size))
        self.assertIsInstance(context_input_ids, torch.Tensor)
        self.assertIsInstance(context_attention_mask, torch.Tensor)
        self.assertIsInstance(retrieved_doc_embeds, torch.Tensor)
