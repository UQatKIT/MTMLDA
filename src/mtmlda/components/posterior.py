"""Components for setting up a Log-posterior model from sub-components.

This module provides different components that can be combined into a log-posterior model. Each of
them mimics the UM-Bridge interface.

Classes:
    GaussianLLFromPTOMap: Gaussian log-likelihood from parameter-to-observable map
    LogPosterior: Wrapper for combining log-prior and log-likelihood into a log-posterior
"""

from typing import Any

import numpy as np
import umbridge as ub


# ==================================================================================================
class GaussianLLFromPTOMap:
    """Gaussian Log-Likelihood.

    This components creates a Gaussian log-likelihood from an UM-Bridge model that constitute a
    parameter-to-observable (PTO) map.

    Methods:
        __call__: UM-Bridge-like call interface for the log-likelihood.
    """

    def __init__(
        self, umbridge_pto_map: ub.Model, data: np.ndarray, covariance: np.ndarray
    ) -> None:
        """Constructor.

        Args:
            umbridge_pto_map (ub.Model): UM-Bridge-server resembling the
                parameter-to-observable map.
            data (np.ndarray): Observable data to compute the misfit with, compared to the PTO
                output
            covariance (np.ndarray): Covariance for waiting of the misfit vector in Gaussian
                likelihood

        Raises:
            ValueError: Checks if the sizes of the data vector and the output of the PTO map match
            ValueError: Checks if the covariance matrix has the same shape as the data vector
        """
        if not umbridge_pto_map.get_output_sizes()[0] == data.size:
            raise ValueError(
                "The size of the data vector does not match the size of the output of the "
                "parameter-to-observable map."
            )
        if not covariance.shape == (data.size, data.size):
            raise ValueError("The covariance matrix must have the same shape as the data vector.")

        self._umbridge_pto_map = umbridge_pto_map
        self._data = data
        self._precision = np.linalg.inv(covariance)

    def __call__(self, parameter: list[list[float]], config: dict[Any]) -> list[list[float]]:
        """UMbridge-like call interface for log-likelihood.

        Note that input- and output-formats of this method resemble exactly those in UM-Bridge.

        Args:
            parameter (list[list[float]]): Parameter candidate
            config (dict[str, Any]): Configuration dictionary to be passed on to UM-Bridge server

        Returns:
            list[list[float]]: Log-likelihood value
        """
        observables = np.array(self._umbridge_pto_map(parameter, config)[0])
        misfit = self._data - observables
        log_likelihood = -0.5 * misfit.T @ self._precision @ misfit
        return [[log_likelihood]]


# ==================================================================================================
class LogPosterior:
    def __init__(self, log_prior: Any, log_likelihood: Any) -> None:
        """Wrapper for computing the log posterior from a log-likelihood and a log-prior component.

        log-likelihood and log-prior can be anything, but need to be callable according to the
        UM-Bridge call interface.

        Args:
            log_prior (Any): Log-prior component
            log_likelihood (Any): Log-likelihood component
        """
        self._log_prior = log_prior
        self._log_likelihood = log_likelihood

    def __call__(
        self, parameter: list[list[float]], **log_likelihood_args: dict[Any]
    ) -> list[list[float]]:
        """UMbridge-like call interface for log-posterior.

        Simply returns the sum of log-likelihood and log-prior values.
        Note that input- and output-formats of this method resemble exactly those in UM-Bridge.
        If the log-prior evaluates to -inf, meaning it doesn't have support for the current
        parameter candidate, likelihood evaluation is not conducted, and -inf returned immediately
        for the log-posterior.

        Args:
            parameter (list[list[float]]): Parameter candidate

        Returns:
            list[list[float]]: Log-posterior value
        """
        log_prior = self._log_prior(parameter)
        if np.isneginf(log_prior[0][0]):
            log_posterior = log_prior
        else:
            log_likelihood = self._log_likelihood(parameter, **log_likelihood_args)
            log_posterior = [[log_likelihood[0][0] + log_prior[0][0]]]

        return log_posterior
