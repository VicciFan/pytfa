# -*- coding: utf-8 -*-
"""
.. module:: pytfa
   :platform: Unix, Windows
   :synopsis: Thermodynamic constraints for Flux-Based Analysis of reactions

.. moduleauthor:: pyTFA team

Relaxation of models with constraint too tight

"""

from collections import OrderedDict
from copy import deepcopy

import pandas as pd
from cobra.util.solver import set_objective
from optlang.exceptions import SolverError

from .constraints import NegativeDeltaG
from .utils import get_solution_value_for_variables
from .variables import PosSlackVariable, NegSlackVariable, DeltaGstd, \
    LogConcentration, NegSlackLC, PosSlackLC
from ..utils import numerics

BIGM = numerics.BIGM
BIGM_THERMO = numerics.BIGM_THERMO
BIGM_DG = numerics.BIGM_DG
BIGM_P = numerics.BIGM_P
EPSILON = numerics.EPSILON

def relax_dgo(tmodel, reactions_to_ignore = [], solver = 'optlang-glpk'):
    """

    :param in_tmodel:
    :type in_tmodel: pytfa.core.ThermoModel:
    :param min_objective_value: 
    :return: 
    """

    # Create a copy of the model on which we will perform the slack addition
    slack_model = deepcopy(tmodel)
    slack_model.solver = solver

    # Create a copy that will receive the relaxation
    relaxed_model = deepcopy(tmodel)
    relaxed_model.solver = solver

    # Do not relax if model is already optimal
    try:
        solution = tmodel.optimize()
    except SolverError as SE:
        status = tmodel.solver.status
        tmodel.logger.error(SE)
        tmodel.logger.warning('Solver status: {}'.format(status))
    # if tmodel.solver.status == OPTIMAL:
    #     raise Exception('Model is already optimal')

    # Find variables that represent standard Gibbs Energy change
    my_dgo = relaxed_model.get_variables_of_type(DeltaGstd)

    # Find constraints that represent negativity of Gibbs Energy change
    my_neg_dg = slack_model.get_constraints_of_type(NegativeDeltaG)

    changes = OrderedDict()
    objective = 0

    for this_neg_dg in my_neg_dg:

        # If there is no thermo, or relaxation forbidden, pass
        if this_neg_dg.id in reactions_to_ignore   \
                or this_neg_dg.id not in my_dgo:
            continue

        # Create the negative and positive slack variables
        neg_slack = slack_model.add_variable(NegSlackVariable,
                                             this_neg_dg.reaction,
                                             lb= 0,
                                             ub= BIGM_DG)
        pos_slack = slack_model.add_variable(PosSlackVariable,
                                             this_neg_dg.reaction,
                                             lb= 0,
                                             ub= BIGM_DG)

        subs_dict = {k: slack_model.variables.get(k.name) \
                     for k in this_neg_dg.constraint.variables}

        # Create the new constraint by adding the slack variables to the
        # negative delta G constraint (from the initial model)
        new_expr = this_neg_dg.constraint.expression.subs(subs_dict)
        new_expr += (pos_slack - neg_slack)

        # Remove former constraint to override it
        slack_model.remove_constraint(this_neg_dg)

        # Add the new variant
        slack_model.add_constraint(NegativeDeltaG,
                                   this_neg_dg.reaction,
                                   expr = new_expr,
                                   lb= 0,
                                   ub= 0)

        # Update the objective with the new variables
        objective += (neg_slack + pos_slack)

    # Change the objective to minimize slack
    set_objective(slack_model,objective)

    # Update variables and constraints references
    slack_model.repair()

    # Relax
    slack_model.objective.direction = 'min'
    relaxation = slack_model.optimize()

    # Extract the relaxation values from the solution, by type
    my_neg_slacks = slack_model.get_variables_of_type(NegSlackVariable)
    my_pos_slacks = slack_model.get_variables_of_type(PosSlackVariable)

    neg_slack_values = get_solution_value_for_variables(relaxation,
                                                        my_neg_slacks)
    pos_slack_values = get_solution_value_for_variables(relaxation,
                                                        my_pos_slacks)


    for this_reaction in relaxed_model.reactions:
        # No thermo, or relaxation forbidden
        if this_reaction.id in reactions_to_ignore \
                or this_reaction.id not in my_dgo:
            continue

        # Get the standard delta G variable
        the_dgo = my_dgo.get_by_id(this_reaction.id)

        # Get the relaxation
        dgo_delta_lb = neg_slack_values[my_neg_slacks \
            .get_by_id(this_reaction.id).name]
        dgo_delta_ub = pos_slack_values[my_pos_slacks \
            .get_by_id(this_reaction.id).name]

        # Apply reaction delta G standard bound change
        if dgo_delta_lb > 0 or dgo_delta_ub > 0:

            # Store previous values
            previous_dgo_lb = the_dgo.variable.lb
            previous_dgo_ub = the_dgo.variable.ub

            # Apply change
            the_dgo.variable.lb -= (dgo_delta_lb + EPSILON)
            the_dgo.variable.ub += (dgo_delta_ub + EPSILON)

            # If needed, store that in a report table
            changes[this_reaction.id] = [
                previous_dgo_lb,
                previous_dgo_ub,
                dgo_delta_lb,
                dgo_delta_ub,
                the_dgo.variable.lb,
                the_dgo.variable.ub]

    # Obtain relaxation
    relaxed_model.optimize()

    # Format relaxation
    relax_table = pd.DataFrame.from_dict(changes,
                                         orient = 'index')
    relax_table.columns = [ 'lb_in',
                            'ub_in',
                            'lb_change',
                            'ub_change',
                            'lb_out',
                            'ub_out']

    tmodel.logger.info('\n' + relax_table.__str__())

    return relaxed_model, slack_model, relax_table

def relax_lc(tmodel, metabolites_to_ignore = [], solver ='optlang-glpk'):
    """

    :param metabolites_to_ignore:
    :param in_tmodel:
    :type in_tmodel: pytfa.core.ThermoModel:
    :param min_objective_value:
    :return:
    """

    # Create a copy of the model on which we will perform the slack addition
    slack_model = deepcopy(tmodel)
    slack_model.solver = solver

    # Create a copy that will receive the relaxation
    relaxed_model = deepcopy(tmodel)
    relaxed_model.solver = solver

    # Do not relax if model is already optimal
    try:
        solution = tmodel.optimize()
    except SolverError as SE:
        status = tmodel.solver.status
        tmodel.logger.error(SE)
        tmodel.logger.warning('Solver status: {}'.format(status))
    # if tmodel.solver.status == OPTIMAL:
    #     raise Exception('Model is already optimal')

    # Find variables that represent standard Gibbs Energy change
    my_lc = relaxed_model.get_variables_of_type(LogConcentration)

    # Find constraints that represent negativity of Gibbs Energy change
    my_neg_dg = slack_model.get_constraints_of_type(NegativeDeltaG)

    changes = OrderedDict()
    objective = 0

    pos_slack = dict()
    neg_slack = dict()

    for this_lc in my_lc:
        if this_lc.name in metabolites_to_ignore:
            continue

        neg_slack[this_lc.name] = slack_model.add_variable(NegSlackLC,
                                             this_lc,
                                             lb= 0,
                                             ub= BIGM_DG)

        pos_slack[this_lc.name] = slack_model.add_variable(PosSlackLC,
                                             this_lc,
                                             lb= 0,
                                             ub= BIGM_DG)

        # Update the objective with the new variables
        objective += (neg_slack[this_lc.name] + pos_slack[this_lc.name])

    for this_neg_dg in my_neg_dg:

        # If there is no thermo, or relaxation forbidden, pass
        if this_neg_dg.id not in my_neg_dg:
            continue


        subs_dict = {k: slack_model.variables.get(k.name) \
                     for k in this_neg_dg.constraint.variables}

        # Create the new constraint by adding the slack variables to the
        # negative delta G constraint (from the initial model)
        new_expr = this_neg_dg.constraint.expression.subs(subs_dict)

        for this_var in this_neg_dg.constraint.variables:
            if not this_var.name in neg_slack:
                continue

            met_id = pos_slack[this_var.name].id
            the_met = slack_model.metabolites.get_by_id(met_id)
            stoich = this_neg_dg.reaction.metabolites[the_met]
            new_expr += slack_model.RT * stoich \
                        * (pos_slack[this_var.name] - neg_slack[this_var.name])

        # Remove former constraint to override it
        slack_model.remove_constraint(this_neg_dg)

        # Add the new variant
        slack_model.add_constraint(NegativeDeltaG,
                                   this_neg_dg.reaction,
                                   expr = new_expr,
                                   lb= 0,
                                   ub= 0)


    # Change the objective to minimize slack
    set_objective(slack_model,objective)

    # Update variables and constraints references
    slack_model.repair()

    # Relax
    slack_model.objective.direction = 'min'
    relaxation = slack_model.optimize()

    # Extract the relaxation values from the solution, by type
    my_neg_slacks = slack_model.get_variables_of_type(NegSlackLC)
    my_pos_slacks = slack_model.get_variables_of_type(PosSlackLC)

    neg_slack_values = get_solution_value_for_variables(relaxation,
                                                        my_neg_slacks)
    pos_slack_values = get_solution_value_for_variables(relaxation,
                                                        my_pos_slacks)


    for this_met in relaxed_model.metabolites:
        # No thermo, or relaxation forbidden
        if this_met.id in metabolites_to_ignore \
                or this_met.id not in my_lc:
            continue

        # Get the standard delta G variable
        the_lc = my_lc.get_by_id(this_met.id)

        # Get the relaxation
        lc_delta_lb = neg_slack_values[my_neg_slacks \
            .get_by_id(this_met.id).name]
        lc_delta_ub = pos_slack_values[my_pos_slacks \
            .get_by_id(this_met.id).name]

        # Apply reaction delta G standard bound change
        if lc_delta_lb > 0 or lc_delta_ub > 0:

            # Store previous values
            previous_lc_lb = the_lc.variable.lb
            previous_lc_ub = the_lc.variable.ub

            # Apply change
            the_lc.variable.lb -= (lc_delta_lb + EPSILON)
            the_lc.variable.ub += (lc_delta_ub + EPSILON)

            # If needed, store that in a report table
            changes[this_met.id] = [
                previous_lc_lb,
                previous_lc_ub,
                lc_delta_lb,
                lc_delta_ub,
                the_lc.variable.lb,
                the_lc.variable.ub]

    # Obtain relaxation
    relaxed_model.optimize()

    # Format relaxation
    relax_table = pd.DataFrame.from_dict(changes,
                                         orient = 'index')
    relax_table.columns = [ 'lb_in',
                            'ub_in',
                            'lb_change',
                            'ub_change',
                            'lb_out',
                            'ub_out']

    tmodel.logger.info('\n' + relax_table.__str__())

    return relaxed_model, slack_model, relax_table