"""Chain-Parallel MTMLDA Runner.

The Runner class provides a wrapper for running parallel chains of the MTMLDA sampler. It unifies
generic MTMLDA components with application specific components. The latter are provided by an
application builder that relies on an interface contract. This approach allows for a wide range
applications that can be interfaced to MTMLDA via this runner.
The runner constructs all generic and problem-specific components from respective settings files.
It further adjust settings for parallel runs depending on the invoking process id,
and initializes the chains accordingly. Parallel runs are conducted in a multiprocessing pool.

Classes:
    ParallelRunSettings: Data class for parallel run settings
    ParallelRunner: Runner class for parallel MTMLDA runs
"""

import multiprocessing
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from mtmlda import utilities as utils
from mtmlda.components import abstract_builder
from mtmlda.core import logging, mltree, sampling


# ==================================================================================================
@dataclass
class ParallelRunSettings:
    """Data class for parallel run settings, used for the `run.py` wrapper.

    Attributes:
        num_chains (int): Number of parallel chains to run
        chain_save_path (Path): Path to save MCMC chain data, will be appended by process ID
        chain_load_path (Path): Path to load MCMC chain data, will be appended by process ID
        node_save_path (Path): Path to save node data, will be appended by process ID
        node_load_path (Path): Path to load node data, will be appended by process ID
        rng_state_save_path (Path): Path to save RNG state data, will be appended by process ID
        rng_state_load_path (Path): Path to load RNG state data, will be appended by process ID
        overwrite_chain (bool): Overwrite existing chain data
        overwrite_node (bool): Overwrite existing node data
        overwrite_rng_states (bool): Overwrite existing RNG state data
    """

    num_chains: int
    chain_save_path: Path
    chain_load_path: Path = None
    node_save_path: Path = None
    node_load_path: Path = None
    rng_state_save_path: Path = None
    rng_state_load_path: Path = None
    overwrite_chain: bool = True
    overwrite_node: bool = True
    overwrite_rng_states: bool = True


# ----------------------------------------------------------------------------------------------
class ParallelRunner:
    """Parllel runner for MTMLDA sampling.

    Methods:
        run: main interface to run parallel sampling
    """

    def __init__(
        self,
        application_builder: abstract_builder.ApplicationBuilder,
        parallel_run_settings: ParallelRunSettings,
        sampler_setup_settings: sampling.SamplerSetupSettings,
        sampler_run_settings: sampling.SamplerRunSettings,
        logger_settings: logging.LoggerSettings,
        inverse_problem_settings: abstract_builder.InverseProblemSettings,
        sampler_component_settings: abstract_builder.SamplerComponentSettings,
        initial_state_settings: abstract_builder.InitialStateSettings,
    ) -> None:
        """Constructor of the parallel runner.

        Takes in all relevant settings for setup and execution of the parallel MTMLDA.

        Args:
            application_builder (abstract_builder.ApplicationBuilder): Builder for the application
                that delivers problem-specific components for the sampler
            parallel_run_settings (ParallelRunSettings): Settings for parallel sampling of
                multiple chains
            sampler_setup_settings (sampling.SamplerSetupSettings): Settings for the setup of the
                MTMLDA sampler object
            sampler_run_settings (sampling.SamplerRunSettings): Settings for starting a run with the
                MTMLDA sampler object
            logger_settings (logging.LoggerSettings): Settings for logging during sampling
            inverse_problem_settings (abstract_builder.InverseProblemSettings): Settings for the
                (posterior) distribution setup of the Application constructed by the builder.
            sampler_component_settings (abstract_builder.SamplerComponentSettings): Settings for the
                sampler component setup provided by the application builder
            initial_state_settings (abstract_builder.InitialStateSettings): Settings for
                initialization of the Markov chains as conducted by the application builder
        """
        self._application_builder = application_builder
        self._parallel_run_settings = parallel_run_settings
        self._sampler_setup_settings = sampler_setup_settings
        self._sampler_run_settings = sampler_run_settings
        self._logger_settings = logger_settings
        self._inverse_problem_settings = inverse_problem_settings
        self._sampler_component_settings = sampler_component_settings
        self._initial_state_settings = initial_state_settings

    # ----------------------------------------------------------------------------------------------
    def run(self) -> None:
        """Runs the function `_execute_mtmlda_on_procs` in a multiprocessing pool."""
        num_chains = self._parallel_run_settings.num_chains
        if num_chains < 1:
            raise ValueError("Number of chains must be at least 1")
        process_ids = range(num_chains)

        with multiprocessing.Pool(processes=num_chains) as process_pool:
            process_pool.map(self._execute_mtmlda_on_procs, process_ids)

    # ----------------------------------------------------------------------------------------------
    def _execute_mtmlda_on_procs(self, process_id: int) -> None:
        """Executes parallel MTMLDA chains.

        This is the main execution method, which is mapped to the workers of a multiprocessing pool
        in `run`. Execution steps are as follows:
        1. Modify settings depending on process ID
        2. Set up application components
        3. Set up sampler components
        4. Adjust initialization settings based on process ID
        5. Run the sampler
        6. Save results to disk

        Args:
            process_id (int): Id of the invoking process
        """
        self._modify_process_dependent_settings(process_id)
        app_builder = self._application_builder(process_id)
        models = app_builder.set_up_models(self._inverse_problem_settings)
        ground_proposal, accept_rate_estimator = app_builder.set_up_sampler_components(
            self._sampler_component_settings
        )
        mtmlda_sampler = sampling.MTMLDASampler(
            self._sampler_setup_settings,
            self._logger_settings,
            models,
            accept_rate_estimator,
            ground_proposal,
        )
        self._adjust_initialization_settings(process_id, app_builder, mtmlda_sampler)
        mcmc_chain, final_node = mtmlda_sampler.run(self._sampler_run_settings)
        rng_states = mtmlda_sampler.get_rngs()
        self._save_results(process_id, rng_states, mcmc_chain, final_node)

    # ----------------------------------------------------------------------------------------------
    def _modify_process_dependent_settings(self, process_id: int) -> None:
        """Modify settings depending on process ID.

        Sets up process-specific paths and RNG seeds. Also disables printing for all but the first
        process.

        Args:
            process_id (int): Invoking process
        """
        # Adjust RNG seeds according to invoking process
        self._sampler_setup_settings.rng_seed_mltree = utils.distribute_rng_seeds_to_processes(
            self._sampler_setup_settings.rng_seed_mltree, process_id
        )
        self._sampler_setup_settings.rng_seed_node_init = utils.distribute_rng_seeds_to_processes(
            self._sampler_setup_settings.rng_seed_node_init, process_id
        )

        # Adjust file paths based on process id
        if self._logger_settings.logfile_path is not None:
            self._logger_settings.logfile_path = utils.append_string_to_path(
                self._logger_settings.logfile_path, f"chain_{process_id}.log"
            )
        if self._logger_settings.debugfile_path is not None:
            self._logger_settings.debugfile_path = utils.append_string_to_path(
                self._logger_settings.debugfile_path, f"chain_{process_id}.log"
            )
        if self._sampler_setup_settings.mltree_path is not None:
            self._sampler_setup_settings.mltree_path = utils.append_string_to_path(
                self._sampler_setup_settings.mltree_path, f"chain_{process_id}"
            )

        # Enable printing only on proc 0
        if process_id != 0:
            self._logger_settings.do_printing = False

    # ----------------------------------------------------------------------------------------------
    def _adjust_initialization_settings(
        self,
        process_id: int,
        app_builder: abstract_builder.ApplicationBuilder,
        mtmlda_sampler: sampling.MTMLDASampler,
    ) -> None:
        """Adjust settings for initialization of the Markov chain, depending on the process id.

        Every process saves result in a different state, so the initialization of the chain
        has to take that into account as well. This method is specific to initialization of a chain,
        after all components have been constructed. Other process-specific settings are adjusted in
        the `_modify_process_dependent_settings` method.

        Args:
            process_id (int): Id pf the invoking process in parallel runs
            app_builder (abstract_builder.ApplicationBuilder): Builder of the application from which
                problem-specific components are generated
            mtmlda_sampler (sampling.MTMLDASampler): MTMLDA Sampler Object
        """
        # Load RNG states
        if self._parallel_run_settings.rng_state_load_path is not None:
            rng_states = utils.load_pickle(
                process_id, self._parallel_run_settings.rng_state_load_path
            )
            mtmlda_sampler.set_rngs(rng_states)

        # Load iniital state from previous chain
        if self._parallel_run_settings.chain_load_path is not None:
            chain = utils.load_chain(process_id, self._parallel_run_settings.chain_load_path)
            initial_state = chain[-1, :]
        else:
            initial_state = app_builder.generate_initial_state(self._initial_state_settings)
        self._sampler_run_settings.initial_state = initial_state
        # Load initial state from tree node
        if self._parallel_run_settings.node_load_path is not None:
            initial_node = utils.load_pickle(process_id, self._parallel_run_settings.node_load_path)
            self._sampler_run_settings.initial_node = initial_node

    # ----------------------------------------------------------------------------------------------
    def _save_results(
        self,
        process_id: int,
        rng_states: sampling.RNGStates,
        mcmc_chain: np.ndarray,
        final_node: mltree.MTNode,
    ) -> None:
        """Save data from sampling to disk.

        All data is stored in process-dependent files. Numeric arrays are stored in npy format,
        other data is pickled.

        Args:
            process_id (int): Id of the invoking process in parallel runs
            rng_states (sampling.RNGStates): RNG states of the Sampler, save for initialization
                to extend previously sampled chains
            mcmc_chain (np.ndarray): Generated samples
            final_node (mltree.MTNode): Last node in the utilized Markov tree, use to restart
                sampling from this point
        """
        if self._parallel_run_settings.rng_state_save_path is not None:
            utils.save_pickle(
                process_id,
                self._parallel_run_settings.rng_state_save_path,
                rng_states,
                exist_ok=self._parallel_run_settings.overwrite_rng_states,
            )
        if self._parallel_run_settings.chain_save_path is not None:
            utils.save_chain(
                process_id,
                self._parallel_run_settings.chain_save_path,
                mcmc_chain,
                exist_ok=self._parallel_run_settings.overwrite_chain,
            )
        if self._parallel_run_settings.node_save_path is not None:
            utils.save_pickle(
                process_id,
                self._parallel_run_settings.node_save_path,
                final_node,
                exist_ok=self._parallel_run_settings.overwrite_node,
            )
