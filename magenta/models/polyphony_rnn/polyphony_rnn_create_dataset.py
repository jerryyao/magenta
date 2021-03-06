# Copyright 2016 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Create a dataset of SequenceExamples from NoteSequence protos.

This script will extract polyphonic tracks from NoteSequence protos and save
them to TensorFlow's SequenceExample protos for input to the polyphonic RNN
models.
"""

import os

# internal imports

import tensorflow as tf

from magenta.models.polyphony_rnn import polyphony_lib
from magenta.models.polyphony_rnn import polyphony_model

from magenta.music import encoder_decoder
from magenta.music import sequences_lib
from magenta.pipelines import dag_pipeline
from magenta.pipelines import pipeline
from magenta.pipelines import pipelines_common
from magenta.protobuf import music_pb2

FLAGS = tf.app.flags.FLAGS
tf.app.flags.DEFINE_string('input', None,
                           'TFRecord to read NoteSequence protos from.')
tf.app.flags.DEFINE_string('output_dir', None,
                           'Directory to write training and eval TFRecord '
                           'files. The TFRecord files are populated with '
                           'SequenceExample protos.')
tf.app.flags.DEFINE_float('eval_ratio', 0.1,
                          'Fraction of input to set aside for eval set. '
                          'Partition is randomly selected.')
tf.app.flags.DEFINE_string('log', 'INFO',
                           'The threshold for what messages will be logged '
                           'DEBUG, INFO, WARN, ERROR, or FATAL.')


class PolyphonicSequenceExtractor(pipeline.Pipeline):
  """Extracts polyphonic tracks from a quantized NoteSequence."""

  def __init__(self, min_steps, max_steps, name=None):
    super(PolyphonicSequenceExtractor, self).__init__(
        input_type=music_pb2.NoteSequence,
        output_type=polyphony_lib.PolyphonicSequence,
        name=name)
    self._min_steps = min_steps
    self._max_steps = max_steps

  def transform(self, quantized_sequence):
    poly_seqs, stats = polyphony_lib.extract_polyphonic_sequences(
        quantized_sequence,
        min_steps_discard=self._min_steps,
        max_steps_discard=self._max_steps)
    self._set_stats(stats)
    return poly_seqs


def get_pipeline(config, steps_per_quarter, min_steps, max_steps, eval_ratio):
  """Returns the Pipeline instance which creates the RNN dataset.

  Args:
    config: An EventSequenceRnnConfig.
    steps_per_quarter: How many steps per quarter to use when quantizing.
    min_steps: Minimum number of steps for an extracted sequence.
    max_steps: Maximum number of steps for an extracted sequence.
    eval_ratio: Fraction of input to set aside for evaluation set.

  Returns:
    A pipeline.Pipeline instance.
  """
  quantizer = pipelines_common.Quantizer(steps_per_quarter=steps_per_quarter)
  # Transpose up to a major third in either direction.
  # Because our current dataset is Bach chorales, transposing more than a major
  # third in either direction probably doesn't makes sense (e.g., because it is
  # likely to exceed normal singing range).
  transposition_range = range(-4, 5)
  transposition_pipeline_train = sequences_lib.TranspositionPipeline(
      transposition_range, name='TranspositionPipelineTrain')
  transposition_pipeline_eval = sequences_lib.TranspositionPipeline(
      transposition_range, name='TranspositionPipelineEval')
  poly_extractor_train = PolyphonicSequenceExtractor(
      min_steps=min_steps, max_steps=max_steps, name='PolyExtractorTrain')
  poly_extractor_eval = PolyphonicSequenceExtractor(
      min_steps=min_steps, max_steps=max_steps, name='PolyExtractorEval')
  encoder_pipeline_train = encoder_decoder.EncoderPipeline(
      polyphony_lib.PolyphonicSequence, config.encoder_decoder,
      name='EncoderPipelineTrain')
  encoder_pipeline_eval = encoder_decoder.EncoderPipeline(
      polyphony_lib.PolyphonicSequence, config.encoder_decoder,
      name='EncoderPipelineEval')
  partitioner = pipelines_common.RandomPartition(
      music_pb2.NoteSequence,
      ['eval_poly_tracks', 'training_poly_tracks'],
      [eval_ratio])

  dag = {quantizer: dag_pipeline.DagInput(music_pb2.NoteSequence),
         partitioner: quantizer,
         transposition_pipeline_train: partitioner['training_poly_tracks'],
         transposition_pipeline_eval: partitioner['eval_poly_tracks'],
         poly_extractor_train: transposition_pipeline_train,
         poly_extractor_eval: transposition_pipeline_eval,
         encoder_pipeline_train: poly_extractor_train,
         encoder_pipeline_eval: poly_extractor_eval,
         dag_pipeline.DagOutput('training_poly_tracks'): encoder_pipeline_train,
         dag_pipeline.DagOutput('eval_poly_tracks'): encoder_pipeline_eval}
  return dag_pipeline.DAGPipeline(dag)


def main(unused_argv):
  tf.logging.set_verbosity(FLAGS.log)

  pipeline_instance = get_pipeline(
      steps_per_quarter=4,
      min_steps=80,  # 5 measures
      max_steps=512,
      eval_ratio=FLAGS.eval_ratio,
      config=polyphony_model.default_configs['polyphony'])

  input_dir = os.path.expanduser(FLAGS.input)
  output_dir = os.path.expanduser(FLAGS.output_dir)
  pipeline.run_pipeline_serial(
      pipeline_instance,
      pipeline.tf_record_iterator(input_dir, pipeline_instance.input_type),
      output_dir)


def console_entry_point():
  tf.app.run(main)


if __name__ == '__main__':
  console_entry_point()
