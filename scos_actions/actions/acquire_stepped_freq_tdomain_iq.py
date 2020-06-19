# What follows is a parameterizable description of the algorithm used by this
# action. The first line is the summary and should be written in plain text.
# Everything following that is the extended description, which can be written
# in Markdown and MathJax. Each name in curly brackets '{}' will be replaced
# with the value specified in the `description` method which can be found at
# the very bottom of this file. Since this parameterization step affects
# everything in curly brackets, math notation such as {m \over n} must be
# escaped to {{m \over n}}.
#
# To print out this docstring after parameterization, see
# REPO_ROOT/scripts/print_action_docstring.py. You can then paste that into the
# SCOS Markdown Editor (link below) to see the final rendering.
#
# Resources:
# - MathJax reference: https://math.meta.stackexchange.com/q/5020
# - Markdown reference: https://commonmark.org/help/
# - SCOS Markdown Editor: https://ntia.github.io/scos-md-editor/
#
r"""Capture time-domain IQ samples at the following {num_center_frequencies} frequencies: {center_frequencies}.

# {name}

## Radio setup and sample acquisition

Each time this task runs, the following process is followed:

{acquisition_plan}

This will take a minimum of {min_duration_ms:.2f} ms, not including radio
tuning, dropping samples after retunes, and data storage.

## Time-domain processing

If specified, a voltage scaling factor is applied to the complex time-domain
signals.

## Data Archive

Each capture will be ${total_samples}\; \text{{samples}} \times 8\;
\text{{bytes per sample}} = {filesize_mb:.2f}\; \text{{MB}}$ plus metadata.

"""

import logging
from itertools import zip_longest

import numpy as np

from scos_actions import utils
from scos_actions.actions import sigmf_builder as scos_actions_sigmf
from scos_actions.actions.interfaces.action import Action
from scos_actions.actions.interfaces.signals import measurement_action_completed
from scos_actions.actions.measurement_params import MeasurementParams
from scos_actions.actions.sigmf_builder import SigMFBuilder

logger = logging.getLogger(__name__)


class SteppedFrequencyTimeDomainIqAcquisition(SingleFrequencyTimeDomainIqAcquisition):
    """Acquire IQ data at each of the requested frequecies.

    :param name: the name of the action
    :param fcs: an iterable of center frequencies in Hz
    :param gains: requested gain in dB, per center_frequency
    :param sample_rates: iterable of sample_rates in Hz, per center_frequency
    :param durations_ms: duration to acquire in ms, per center_frequency
    :param radio: instance of RadioInterface

    """

    def __init__(self, parameters, radio):
        super(SteppedFrequencyTimeDomainIqAcquisition, self).__init__()
        self.parameters = parameters
        self.sorted_measurement_parameters = []
        num_center_frequencies = len(self.parameters["fcs"])

        parameter_names = ("center_frequency", "gain", "sample_rate", "duration_ms")

        # Sort combined parameter list by frequency
        def sortFrequency(zipped_params):
            return zipped_params[0]

        sorted_params = list(zip_longest(self.parameters["fcs"], self.parameters["gains"], self.parameters["sample_rates"], self.parameters["durations_ms"]))
        sorted_params.sort(key=sortFrequency)

        for params in sorted_params:
            if None in params:
                param_name = parameter_names[params.index(None)]
                err = "Wrong number of {}s, expected {}"
                raise TypeError(err.format(param_name, num_center_frequencies))

                self.sorted_measurement_parameters.append(dict(zip(parameter_names, params)))

        self.radio = radio  # make instance variable to allow mocking
        self.num_center_frequencies = num_center_frequencies

    def __call__(self, schedule_entry_json, task_id, sensor_definition):
        """This is the entrypoint function called by the scheduler."""
        self.test_required_components()

        for recording_id, measurement_params in enumerate(
            self.sorted_measurement_parameters, start=1
        ):
            start_time = utils.get_datetime_str_now()
            #data = self.acquire_data(measurement_params)
            measurement_result = super().acquire_data(measurement_params)
            end_time = utils.get_datetime_str_now()
            received_samples = len(measurement_result["data"])
            # sigmf_builder = super().build_sigmf_md(
            #     start_time,
            #     end_time,
            #     self.radio.capture_time,
            #     schedule_entry_json,
            #     sensor_definition,
            #     task_id,
            #     recording_id
            # )
            sigmf_builder = SigMFBuilder()
            self.get_sigmf_global(sigmf_builder, schedule_entry_json, sensor_definition, measurement_result, start_time, end_time, task_id)
            self.get_sigmf_global_measurement(sigmf_builder, start_time, end_time, measurement_result, recording_id)
            self.get_sigmf_capture(sigmf_builder, measurement_result)
            self.get_sigmf_annotation(sigmf_builder, measurement_result)
            measurement_action_completed.send(
                sender=self.__class__,
                task_id=task_id,
                data=measurement_result["data"],
                metadata=sigmf_builder.metadata,
            )

    @property
    def is_multirecording(self):
        return len(self.sorted_measurement_parameters) > 1

    @property
    def description(self):
        """Parameterize and return the module-level docstring."""

        acquisition_plan = ""
        acq_plan_template = "1. Tune to {fc_MHz:.2f} MHz, "
        acq_plan_template += "set gain to {gain} dB, "
        acq_plan_template += "and acquire at {sample_rate_Msps:.2f} Msps "
        acq_plan_template += "for {duration_ms} ms\n"

        total_samples = 0
        for measurement_params in self.measurement_params_list:
            acq_plan_template.format(
                **{
                    "fc_MHz": measurement_params.center_frequency / 1e6,
                    "gain": measurement_params.gain,
                    "sample_rate_Msps": measurement_params.sample_rate / 1e6,
                    "duration_ms": measurement_params.duration_ms,
                }
            )
            total_samples += int(
                measurement_params.duration_ms / 1e6 * measurement_params.sample_rate
            )

        f_low = self.measurement_params_list[0].center_frequency
        f_low_srate = self.measurement_params_list[0].sample_rate
        f_low_edge = (f_low - f_low_srate / 2.0) / 1e6

        f_high = self.measurement_params_list[-1].center_frequency
        f_high_srate = self.measurement_params_list[-1].sample_rate
        f_high_edge = (f_high - f_high_srate / 2.0) / 1e6

        durations = [v.duration_ms for v in self.measurement_params_list]
        min_duration_ms = np.sum(durations)

        filesize_mb = total_samples * 8 / 1e6  # 8 bytes per complex64 sample

        defs = {
            "name": self.name,
            "num_center_frequencies": self.num_center_frequencies,
            "center_frequencies": ", ".join(
                [
                    "{:.2f} MHz".format(param.center_frequency / 1e6)
                    for param in self.measurement_params_list
                ]
            ),
            "acquisition_plan": acquisition_plan,
            "min_duration_ms": min_duration_ms,
            "total_samples": total_samples,
            "filesize_mb": filesize_mb,
        }

        # __doc__ refers to the module docstring at the top of the file
        return __doc__.format(**defs)
