import os
import json

import numpy as np

import pyrosetta
from pyrosetta import rosetta

from local_protein_sequence_design import site_settings
from local_protein_sequence_design import fragment_quality_analysis
from local_protein_sequence_design import IO 
from local_protein_sequence_design.basic import *


def get_residue_selector_for_residues(residues):
    '''Get a residue selector for a given set of residues.'''
    return rosetta.core.select.residue_selector.ResidueIndexSelector(','.join(list(str(i) for i in residues)))

def residues_average_energy(pose, residues):
    '''Return the average energy of a set of residues.'''
    sfxn = rosetta.core.scoring.get_score_function()
    sfxn(pose)

    return np.mean(list(pose.energies().residue_total_energy(i) for i in residues))

def residues_max_energy(pose, residues):
    '''Return the max energy of a set of residues.'''
    sfxn = rosetta.core.scoring.get_score_function()
    sfxn(pose)

    return max(list(pose.energies().residue_total_energy(i) for i in residues))

def get_backrub_ensemble_consensus_buhs_for_each_res(pose):
    '''Get the list of numbers of ensemble consensus buried unsatisfied
    hbonds for each residue.
    '''
    br_mover = rosetta.protocols.backrub.BackrubMover()
    br_mover.set_max_atoms(4)
    
    buhs_for_each_res = get_buhs_for_each_res(pose)
    
    # Update the number of segments
    
    tmp_pose = pose.clone()
    br_mover.apply(tmp_pose)

    # Iterate throught all segments

    for i in range(1, br_mover.num_segments() + 1):
        br_mover.set_next_segment_id(i)
       
        # For each segment, generate 5 structures
        
        for j in range(5):
            tmp_pose = pose.clone()
            br_mover.apply(tmp_pose)
            tmp_buh_for_each_res = get_buhs_for_each_res(tmp_pose)
           
            # Only keep the consensus buried unsats

            for k in range(len(buhs_for_each_res)):
                buhs_for_each_res[k] = min(buhs_for_each_res[k], tmp_buh_for_each_res[k])

    return buhs_for_each_res

def get_num_buried_unsatisfied_hbonds(pose, residues):
    '''Get the number of buried unsatisfied hbonds
    for each of the given set of residues.
    '''
    bupc = rosetta.protocols.simple_pose_metric_calculators.BuriedUnsatisfiedPolarsCalculator(
            'default', 'default')

    sfxn = rosetta.core.scoring.get_score_function()
    sfxn(pose)
    
    buhs_for_each_res = json.loads(bupc.get('residue_bur_unsat_polars', pose))

    return sum(buhs_for_each_res[i - 1] for i in residues)

def get_num_over_saturated_hbond_acceptors(pose, acceptor_residues):
    '''Get the number of over saturated hbond acceptors for
    a given set of acceptor residues.
    '''
    oshaf = rosetta.protocols.cyclic_peptide.OversaturatedHbondAcceptorFilter()
    oshaf.set_acceptor_selector(get_residue_selector_for_residues(acceptor_residues))
    oshaf.set_consider_mainchain_only(False)

    return oshaf.report_sm(pose)

def get_hydrophobic_sasa_sc(pose, residues):
    '''Calculate the hydrophobic sidechain SASA for a given set of residues.'''
    hydrophobic_residues = ['ALA', 'PRO', 'VAL', 'LEU', 'ILE', 'MET',
                            'PHE', 'TYR', 'TRP']
    
    rsd_sasa = pyrosetta.rosetta.utility.vector1_double()
    rosetta.core.scoring.calc_per_atom_sasa_sc(pose, rsd_sasa, False)
 
    sasa = 0

    for i in residues:
        if pose.residue(i).name3() in hydrophobic_residues:
            sasa += rsd_sasa[i]

    return sasa

def get_sasa(pose, residues):
    '''Calculate the total and hydrophobic sasa for a given set of residues.'''
    rsd_sasa = pyrosetta.rosetta.utility.vector1_double()
    rsd_hydrophobic_sasa = pyrosetta.rosetta.utility.vector1_double()
    rosetta.core.scoring.calc_per_res_hydrophobic_sasa(pose, rsd_sasa, rsd_hydrophobic_sasa, 1.4) #The last arguement is the probe radius

    return sum(rsd_sasa[i] for i in residues), sum(rsd_hydrophobic_sasa[i] for i in residues)

def get_holes_score_for_residues(pose, residues):
    '''Get the holes score for a list of residues.'''
    ss = site_settings.load_site_settings()
    dalphaball = os.path.abspath(ss['dalphaball'])
    rosetta.basic.options.set_file_option('holes:dalphaball', dalphaball)
   
    hf = rosetta.protocols.simple_filters.HolesFilter()
    hf.set_residue_selector(get_residue_selector_for_residues(residues))
    return hf.compute(pose) / sum(pose.residue(i).natoms() for i in residues)

def get_helix_complementarity_score(pose, residues):
    '''Get the helix complementarity score for a list of residues.
    NOTE: there mask be at least one helix in the given region.
    '''
    hscf = rosetta.protocols.denovo_design.filters.SSShapeComplementarityFilter()
    hscf.set_residue_selector(get_residue_selector_for_residues(residues))
    
    return hscf.compute(pose)

def get_fragment_quality_scores(pose, bb_remodeled_residues):
    '''Get the fragment quality scores given
    the backbone remodeled region.

    Return:
        bb_remodeled_worst_fragment_crmsd, bb_remodeled_mean_fragment_crmsd

    NOTE: This function should only be called when the
    current working directory is the directory of the design.
    Also the design.pdb.gz file must exist in the current directory.
    '''
    # Create a fasta file
    
    sequence = pose.sequence()
    IO.sequence_to_fasta_file('design.fasta', 'design', sequence)

    # Do fragment picking
    
    ss = site_settings.load_site_settings()
    fqa = fragment_quality_analysis.FragmentQualityAnalyzer(
           ss['runpsipred_single'], ss['csblast'], ss['blastpgp'], ss['placeholder_seqs'], ss['sparksx_path'],
           ss['fragment_picker'], ss['vall'], ss['fragment_quality_analysis_weights'],
           rosetta_database=ss['rosetta_database_fragment_picking'])

    fdf = fqa.pick_fragments('design.pdb.gz', 'design.fasta', 
            '.', query_pos=bb_remodeled_residues)

    crmsds = fragment_quality_analysis.FragmentQualityAnalyzer.get_position_crmsd(fdf) 

    # Remove temporary files

    os.remove('frags.fsc.200.9mers')

    return max(crmsds), np.mean(crmsds)

def generate_filter_scores(filter_info_file, pose, designable_residues, repackable_residues, bb_remodeled_residues):
    '''Generate the scores of filters and save the scores
    into the filter_info_file in json format.
    '''
    movable_residues = designable_residues + repackable_residues
    all_residues = list(range(1, pose.size()))

    filter_scores = {}

    # Get the average energies

    filter_scores['designable_residues_average_energy'] = residues_average_energy(pose, designable_residues)
    filter_scores['movable_residues_average_energy'] = residues_average_energy(pose, movable_residues)
    filter_scores['all_average_energy'] = residues_average_energy(pose, all_residues)

    # Get the max energy of residues

    filter_scores['designable_residues_max_energy'] = residues_max_energy(pose, designable_residues)
    filter_scores['movable_residues_max_energy'] = residues_max_energy(pose, movable_residues)
    filter_scores['all_residues_max_energy'] = residues_max_energy(pose, all_residues)

    # Get the number of buried unsatisfied hbonds

    filter_scores['buried_unsat_for_designable_residues'] = get_num_buried_unsatisfied_hbonds(pose, designable_residues)
    filter_scores['buried_unsat_for_movable_residues'] = get_num_buried_unsatisfied_hbonds(pose, movable_residues)
    filter_scores['buried_unsat_for_all_residues'] = get_num_buried_unsatisfied_hbonds(pose, all_residues)

    backrub_ensemble_consensus_buhs_for_each_res = get_backrub_ensemble_consensus_buhs_for_each_res(pose)

    filter_scores['backrub_ensemble_consensus_buhs_for_all_residues'] = sum(backrub_ensemble_consensus_buhs_for_each_res)
    filter_scores['backrub_ensemble_consensus_buhs_for_designable_residues'] = sum(backrub_ensemble_consensus_buhs_for_each_res[i + 1] for i in designable_residues)
    filter_scores['backrub_ensemble_consensus_buhs_for_movable_residues'] = sum(backrub_ensemble_consensus_buhs_for_each_res[i + 1] for i in movable_residues)

    # Get the number of over saturated hbond acceptors

    filter_scores['num_over_saturated_hbond_acceptors_for_designable_residues'] = get_num_over_saturated_hbond_acceptors(pose, designable_residues)
    filter_scores['num_over_saturated_hbond_acceptors_for_movable_residues'] = get_num_over_saturated_hbond_acceptors(pose, designable_residues)
    filter_scores['num_over_saturated_hbond_acceptors_for_all_residues'] = get_num_over_saturated_hbond_acceptors(pose, designable_residues)

    # Get the hydrophobic sasa for designable residues

    filter_scores['hydrophobic_sasa_sc_for_designable_residues'] = get_hydrophobic_sasa_sc(pose, designable_residues)

    # Get the hydrophobic sasa for movable residues

    total_sasa, hydrophobic_sasa = get_sasa(pose, designable_residues + repackable_residues)

    filter_scores['hydrophobic_sasa_for_movable_residues'] = hydrophobic_sasa 
    filter_scores['average_hydrophobic_sasa_for_movable_residues'] = hydrophobic_sasa / len(designable_residues + repackable_residues) 
    filter_scores['relative_hydrophobic_sasa_for_movable_residues'] = hydrophobic_sasa / total_sasa

    # Get the local holes score

    filter_scores['designable_local_holes_score'] = get_holes_score_for_residues(pose, designable_residues)
    filter_scores['movable_local_holes_score'] = get_holes_score_for_residues(pose, movable_residues)
    filter_scores['all_local_holes_score'] = get_holes_score_for_residues(pose, all_residues)

    # Get the backbone remodeled residues helixc complementarity score

    filter_scores['bb_remodeled_residues_helix_complementarity'] = get_helix_complementarity_score(pose, bb_remodeled_residues)

    # Get fragment quality analysis scores for the backbone remodeled region
    
    bb_remodeled_worst_fragment_crmsd, bb_remodeled_mean_fragment_crmsd = get_fragment_quality_scores(pose, bb_remodeled_residues)
   
    filter_scores['bb_remodeled_worst_fragment_crmsd'] = bb_remodeled_worst_fragment_crmsd
    filter_scores['bb_remodeled_mean_fragment_crmsd'] = bb_remodeled_mean_fragment_crmsd

    with open(filter_info_file, 'w') as f:
        json.dump(filter_scores, f)

