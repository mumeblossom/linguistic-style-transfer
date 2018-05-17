import argparse
import os
import pickle
import sys

import numpy as np
import tensorflow as tf

from linguistic_style_transfer_model.config import global_config
from linguistic_style_transfer_model.config.options import Options
from linguistic_style_transfer_model.models import adversarial_autoencoder
from linguistic_style_transfer_model.utils import bleu_scorer, \
    data_processor, log_initializer, word_embedder

logger = None


def get_data(options):
    [word_index, padded_sequences, text_sequence_lengths,
     text_tokenizer, inverse_word_index] = \
        data_processor.get_text_sequences(
            options.text_file_path, options.vocab_size, global_config.vocab_size_save_path,
            global_config.text_tokenizer_path, global_config.vocab_save_path)
    logger.debug("text_sequence_lengths: {}".format(text_sequence_lengths.shape))
    logger.debug("padded_sequences: {}".format(padded_sequences.shape))

    [one_hot_labels, num_labels] = \
        data_processor.get_labels(options.label_file_path, True)
    logger.debug("one_hot_labels.shape: {}".format(one_hot_labels.shape))

    return [word_index, padded_sequences, text_sequence_lengths, one_hot_labels, num_labels,
            text_tokenizer, inverse_word_index]


def flush_ground_truth_sentences(actual_sequences, start_index, final_index,
                                 inverse_word_index, timestamped_file_suffix):
    actual_sequences = actual_sequences[start_index:final_index]

    actual_word_lists = \
        [data_processor.generate_words_from_indices(x, inverse_word_index)
         for x in actual_sequences]

    actual_sentences = [" ".join(x) for x in actual_word_lists]

    output_file_path = "output/{}/actual_sentences.txt".format(timestamped_file_suffix)
    os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
    with open(output_file_path, 'w') as output_file:
        for sentence in actual_sentences:
            output_file.write(sentence + "\n")

    return actual_word_lists


def execute_post_inference_operations(
        actual_word_lists, generated_sequences, final_sequence_lengths, overall_label_predictions,
        style_label_predictions, adversarial_label_predictions, inverse_word_index,
        timestamped_file_suffix, mode):
    logger.debug("Minimum generated sentence length: {}".format(min(final_sequence_lengths)))

    # first trims the generates sentences down to the length the decoder returns
    # then trim any <eos> token
    trimmed_generated_sequences = \
        [[index for index in sequence
          if index != global_config.predefined_word_index[global_config.eos_token]]
         for sequence in [x[:(y - 1)] for (x, y) in zip(generated_sequences, final_sequence_lengths)]]

    generated_word_lists = \
        [data_processor.generate_words_from_indices(x, inverse_word_index)
         for x in trimmed_generated_sequences]

    # Evaluate model scores
    bleu_scores = bleu_scorer.get_corpus_bleu_scores(
        [[x] for x in actual_word_lists], generated_word_lists)
    logger.info("bleu_scores: {}".format(bleu_scores))
    generated_sentences = [" ".join(x) for x in generated_word_lists]

    output_file_path = "output/{}-inference/generated_{}.txt".format(timestamped_file_suffix, mode)
    os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
    with open(output_file_path, 'w') as output_file:
        for sentence in generated_sentences:
            output_file.write(sentence + "\n")

    # write label predictions to file
    output_file_path = "output/{}-inference/overall_labels_prediction.txt".format(timestamped_file_suffix)
    os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
    with open(output_file_path, 'w') as output_file:
        for one_hot_label in overall_label_predictions:
            output_file.write("{}\n".format(one_hot_label.tolist().index(1)))

    output_file_path = "output/{}-inference/style_labels_prediction.txt".format(timestamped_file_suffix)
    os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
    with open(output_file_path, 'w') as output_file:
        for one_hot_label in style_label_predictions:
            output_file.write("{}\n".format(one_hot_label.tolist().index(1)))

    output_file_path = "output/{}-inference/adversarial_labels_prediction.txt".format(timestamped_file_suffix)
    os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
    with open(output_file_path, 'w') as output_file:
        for one_hot_label in adversarial_label_predictions:
            output_file.write("{}\n".format(one_hot_label.tolist().index(1)))


def get_word_embeddings(embedding_model_path, word_index):
    encoder_embedding_matrix = np.random.uniform(
        size=(global_config.vocab_size, global_config.embedding_size),
        low=-0.05, high=0.05).astype(dtype=np.float32)
    logger.debug("encoder_embedding_matrix: {}".format(encoder_embedding_matrix.shape))

    decoder_embedding_matrix = np.random.uniform(
        size=(global_config.vocab_size, global_config.embedding_size),
        low=-0.05, high=0.05).astype(dtype=np.float32)
    logger.debug("decoder_embedding_matrix: {}".format(decoder_embedding_matrix.shape))

    if embedding_model_path:
        logger.info("Loading pretrained embeddings")
        encoder_embedding_matrix, decoder_embedding_matrix = \
            word_embedder.add_word_vectors_to_embeddings(
                word_index, encoder_embedding_matrix, decoder_embedding_matrix,
                embedding_model_path)

    return encoder_embedding_matrix, decoder_embedding_matrix


def main(argv):
    options = Options()

    parser = argparse.ArgumentParser()
    parser.add_argument("--logging-level", type=str, default="INFO")
    run_mode = parser.add_mutually_exclusive_group(required=True)
    run_mode.add_argument("--train-model", action="store_true", default=False)
    run_mode.add_argument("--generate-novel-text", action="store_true", default=False)

    parser.parse_known_args(args=argv, namespace=options)
    if options.train_model:
        parser.add_argument("--vocab-size", type=int, default=1000)
        parser.add_argument("--training-epochs", type=int, default=10)
        parser.add_argument("--text-file-path", type=str)
        parser.add_argument("--label-file-path", type=str)
        parser.add_argument("--validation-text-file-path", type=str)
        parser.add_argument("--validation-label-file-path", type=str)
        parser.add_argument("--training-embeddings-file-path", type=str)
        parser.add_argument("--validation-embeddings-file-path", type=str)
        parser.add_argument("--dump-embeddings", action="store_true", default=False)
        parser.add_argument("--classifier-saved-model-path", type=str)
    if options.generate_novel_text:
        parser.add_argument("--saved-model-path", type=str)
        parser.add_argument("--evaluation-text-file-path", type=str)

    parser.parse_known_args(args=argv, namespace=options)

    global logger
    logger = log_initializer.setup_custom_logger(global_config.logger_name, options.logging_level)

    if not (options.train_model or options.generate_novel_text):
        logger.info("Nothing to do. Exiting ...")
        sys.exit(0)

    global_config.training_epochs = options.training_epochs

    # Train and save model
    if options.train_model:
        os.makedirs(global_config.save_directory)

        # Retrieve all data
        logger.info("Reading data ...")
        [word_index, padded_sequences, text_sequence_lengths, one_hot_labels, num_labels,
         text_tokenizer, inverse_word_index] = get_data(options)
        data_size = padded_sequences.shape[0]

        encoder_embedding_matrix, decoder_embedding_matrix = \
            get_word_embeddings(options.training_embeddings_file_path, word_index)

        # Build model
        logger.info("Building model architecture ...")
        network = adversarial_autoencoder.AdversarialAutoencoder()
        network.build_model(
            word_index, encoder_embedding_matrix, decoder_embedding_matrix, num_labels)

        logger.info("Training model ...")
        sess = get_tensorflow_session()

        [_, validation_actual_word_lists, validation_sequences, validation_sequence_lengths] = \
            data_processor.get_test_sequences(
                options.validation_text_file_path, word_index, text_tokenizer, inverse_word_index)
        [_, validation_labels] = \
            data_processor.get_test_labels(options.validation_label_file_path)

        network.train(
            sess, data_size, padded_sequences, text_sequence_lengths, one_hot_labels, num_labels,
            word_index, encoder_embedding_matrix, decoder_embedding_matrix, validation_sequences,
            validation_sequence_lengths, validation_labels, inverse_word_index, validation_actual_word_lists,
            options)
        sess.close()

        average_label_embeddings = data_processor.get_average_label_embeddings(
            data_size, options.dump_embeddings)

        with open(global_config.average_label_embeddings_path, 'wb') as pickle_file:
            pickle.dump(average_label_embeddings, pickle_file)

        logger.info("Training complete!")

    elif options.generate_novel_text:
        # Enforce a particular style embedding and regenerate text
        logger.info("Generating novel text ...")

        with open(os.path.join(options.saved_model_path,
                               global_config.vocab_save_file), 'rb') as pickle_file:
            word_index = pickle.load(pickle_file)
        with open(os.path.join(options.saved_model_path,
                               global_config.text_tokenizer_file), 'rb') as pickle_file:
            text_tokenizer = pickle.load(pickle_file)
        with open(os.path.join(options.saved_model_path,
                               global_config.index_to_label_dict_file), 'rb') as pickle_file:
            index_to_label_map = pickle.load(pickle_file)
        with open(os.path.join(options.saved_model_path,
                               global_config.average_label_embeddings_file), 'rb') as pickle_file:
            average_label_embeddings = pickle.load(pickle_file)
        with open(os.path.join(options.saved_model_path,
                               global_config.vocab_size_save_file), 'rb') as pickle_file:
            global_config.vocab_size = pickle.load(pickle_file)

        num_labels = len(index_to_label_map)

        logger.info("Building model architecture ...")
        network = adversarial_autoencoder.AdversarialAutoencoder()
        encoder_embedding_matrix, decoder_embedding_matrix = get_word_embeddings(None, word_index)
        network.build_model(
            word_index, encoder_embedding_matrix, decoder_embedding_matrix, num_labels)

        sess = get_tensorflow_session()

        for i in range(num_labels):
            logger.info("Style chosen: {}".format(i))

            style_embedding = np.asarray(average_label_embeddings[i])

            inverse_word_index = {v: k for k, v in word_index.items()}
            [actual_sequences, _, padded_sequences, text_sequence_lengths] = \
                data_processor.get_test_sequences(
                    options.evaluation_text_file_path, word_index, text_tokenizer,
                    inverse_word_index)

            generated_sequences, final_sequence_lengths, overall_label_predictions, \
            style_label_predictions, adversarial_label_predictions = \
                network.generate_novel_sentences(
                    sess, padded_sequences, text_sequence_lengths, style_embedding, num_labels,
                    os.path.join(options.saved_model_path, global_config.model_save_file))

            actual_word_lists = \
                [data_processor.generate_words_from_indices(x, inverse_word_index)
                 for x in actual_sequences]

            execute_post_inference_operations(
                actual_word_lists, generated_sequences, final_sequence_lengths,
                overall_label_predictions, style_label_predictions, adversarial_label_predictions,
                inverse_word_index, global_config.experiment_timestamp,
                "novel_sentences_{}".format(i))

            logger.info("Generation complete!")
        sess.close()


def get_tensorflow_session():
    gpu_options = tf.GPUOptions(allow_growth=True)
    config_proto = tf.ConfigProto(
        log_device_placement=False, allow_soft_placement=True,
        gpu_options=gpu_options)

    return tf.Session(config=config_proto)


if __name__ == "__main__":
    main(sys.argv[1:])
