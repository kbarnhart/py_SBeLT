from __future__ import division
import math
import random
import numpy as np
import sympy as sy
import copy

from collections import defaultdict
# from codetiming import Timer
from numba import jit, njit

import logging

# TODO: Consider refactoring from class into simple struct 
class Subregion():
    """ Subregion class.
    
    Each instance of Subregion contains
    the name, and the left and right 
    boundaries of a subregion. 
    
    Name and boundaries are set during 
    instantiation and can be retrieved
    afterwards using helper methods.
    """
    def __init__(self, name, left_boundary, right_boundary, iterations):
        self.name = name
        self.left_boundary = left_boundary
        self.right_boundary = right_boundary
        self.flux_list = np.zeros(iterations, dtype=np.int64)
        
    def leftBoundary(self):
        return self.left_boundary
    
    def rightBoundary(self):
        return self.right_boundary
    
    def getName(self):
        return self.name

    def incrementFlux(self, iteration):
        self.flux_list[iteration] += 1
    
    def getFluxList(self):
        return self.flux_list

        
#%% Bed-related functionss
# TODO: consider if Bed (and Model) functions should be refactored into classes

# @Timer("Get_event_particles", text="get_event_particles call: {:.3f} seconds", logger=None)
def get_event_particles(e_events, subregions, model_particles, level_limit, height_dependant=False):
    """ Find and return list of particles to be entrained

    Keyword arguments:
    e_events -- Number of events requested per subregion 
    subregions -- List of Subregion objects
    model_particles -- The model's model_particles array 

    Returns:
    total_e_events -- Number of events over entire stream
    event_particles -- List of particles to be entrained

    """
    if e_events == 0:
        e_events = 1 #???
    
    event_particles = []
    for subregion in subregions:
        # Filter:
        # Take only particles in current subregion
        subregion_particles = model_particles[
                (model_particles[:,0] >= subregion.leftBoundary())
              & (model_particles[:,0] <= subregion.rightBoundary())]
        # Take only particles in-stream
        in_stream_particles = subregion_particles[
                                                subregion_particles[:,0] != -1]
        # Take only particles set as 'Active'
        active_particles =  in_stream_particles[
                                                in_stream_particles[:,4] != 0]

        # Do not take any particles that have been selected for entrainment 
        # This only happens when particles rest on the boundary. Perhaps make code more obviously directed to this behaviour
        active_event, active_idx, event_idx = np.intersect1d(active_particles[:,3], event_particles, return_indices=True)
        active_particles = np.delete(active_particles, active_idx, axis=0)

        subregion_event_ids = []  
        if height_dependant:
            # TODO: better approach to identify the level/elevation relationship. This is messy 
            levels = elevation_list(subregion_particles[:,2], desc=False)

            tip_particles = []
            if len(levels) == level_limit: # or anything greater #TODO: THIS IS BAD -- active particles != stream elevations
                tip_particles = active_particles[active_particles[:,2] == levels[level_limit-1]]
            for particle in tip_particles:
                subregion_event_ids.append(particle[3])
                active_particles = active_particles[active_particles[:,2] != particle[2]]
                # e_events = e_events - 1
        # if e_events > 0:
        if e_events > len(active_particles):
            random_sample = random.sample(range(len(active_particles)), 
                                        len(active_particles))
        else:
            random_sample = random.sample(range(len(active_particles)), 
                                        e_events)
        # TODO: change so that we don't rely on loop index to grab particle
        for index in random_sample:
            #NOTE: this only works because index=id in the model_particle array
            subregion_event_ids.append(int(active_particles[index][3])  )
        
        ghost_particles = np.where(model_particles[:,0] == -1)[0]
        for index in ghost_particles:
            model_particles[index][0] = 0 
            subregion_event_ids.append(index)
        
        if e_events != len(subregion_event_ids):
            msg = (
                     f'Requested {e_events} events in {subregion.getName()} ' 
                     f'but {len(subregion_event_ids)} are occuring'
            )
            logging.warning(msg)
        event_particles = event_particles + subregion_event_ids
    event_particles = np.array(event_particles, dtype=np.intp)

    return event_particles

# @Timer("define_subregions", text="define_subregions call: {:.3f} seconds", logger=None)
def define_subregions(bed_length, num_subregions, iterations):
    """ Define subregion list for model stream.
    

    Keyword arguments:
    bed_length -- The length of the model bed.
    subregions -- The number of subregions to create.
    iterations -- number of iterations for this simulation

    Returns:
    subregions_arr -- The np array of Subregions

    """
    # try:
    assert(math.remainder(bed_length, num_subregions) == 0)
    # except AssertionError:
    #     raise ValueError(f'Number of subregions needs to be a divisor of the bed length: {bed_length}%{num_subregions} != 0')
    
    subregion_length = bed_length/num_subregions
    left_boundary = 0.0
    subregions_arr = []
    for region in range(num_subregions):  
        right_boundary = left_boundary + subregion_length   
        subregion = Subregion(f'subregion-{region}', left_boundary, right_boundary, iterations)
        left_boundary = right_boundary
        
        subregions_arr.append(subregion)
    
    return subregions_arr
    
# @Timer("build_streambed", text="build_streambed call: {:.5f} seconds", logger=None)
def build_streambed(x_max, set_diam):
    """ Build the bed particle list.
    
    
    Handles calls to add_bed_particle, checks for 
    completness of bed and updates the x-extent
    of stream when the packing exceeds/under packs 
    within 8mm range.
    
    Note: the updates to x-extent are only required 
    when variable particle diameter is being used. 
    
    Return values:
    bed_particles -- list of bed particles
    bed_vertices -- list of available vertices 
                    based on bed list 
    """

    max_particles = int(math.ceil( x_max / set_diam ))
    bed_particles = np.zeros([max_particles, 7],dtype=float)
    
    particle_id = -1
    centre = (set_diam/2)  
    state = 0
    age = 0
    loop_age = 0
    elevation = 0

    # TODO: NumPy improvement: This probably doesn't need to be a while loop
    while not bed_complete(centre, x_max):  
        bed_particles[particle_id] = [centre, set_diam, elevation, particle_id, state, age, loop_age]
        centre += set_diam
        particle_id += -1 # Bed particles get negative IDs
    
    # TODO: This behaviour should be gotten rid of with a check for incompat diams.
    # Bed packing does not always match x_max. Adjust if off
    bed_max = int(math.ceil(bed_particles[(-particle_id)-2][1] 
                            + bed_particles[(-particle_id)-2][3]))
    if x_max != bed_max:
        msg = (
            f'Bed packing could not match x_max parameter... Updating '
            f'x_max to match packing extent: {bed_max}....'
        )
        logging.warning(msg)
        x_max = bed_max
    else: x_max = x_max
    # strip zero element particles tuples from the original array
    valid = ((bed_particles==0).all(axis=(1)))
    bed_particles = bed_particles[~valid]

    return bed_particles, len(bed_particles)*set_diam

def bed_complete(pack_idx, x_max):
    """Check to see if bed is complete based on model params.""" 
    # similarly, if np.count_nonzero(bed_space) == x_max
    if pack_idx >= x_max:
        return 1
    else: return 0
    
# End bed-related function definitions
#%% Entrainment and model particle related functions

def determine_num_particles(pack_frac, num_vertices):
    """Return the number of model particles to be used, based on 
    the packing fraction"""
    
    num_particles = num_vertices * pack_frac
    num_particles = int(math.ceil(num_particles))
    
    return num_particles

# @Timer("place_particle", text="place_particle call: {:.5f} seconds", logger=None)
# Second answer: https://math.stackexchange.com/questions/2293201/
def place_particle(particle, model_particles, bed_particles, h):
    """ Calculate new X and Y of particle based on location in stream.
    
    
    Provided a particle's (pA) location (xA) in stream, 
    search for 2 supporting particles (n1, n2) that pA might
    come into contact with when placed at xA. 
    
    Calculate the Y position of pA based on height of n1, n2 
    supporting particles. The resulting X position 
    will always be xA. 
    
    Keyword arguments:
    placement_idx -- considered particles locaiton (pA)
    particle_diam -- diameter of considered particle
    model_particles -- model particle list
    bed_particles -- bed particle list
    
    """
    # TODO: It would be ideal to avoid a call to find_supports here.
    left_support, right_support = find_supports(particle, model_particles, 
                                                bed_particles, already_placed=False)
    
    return round(particle[0], 2), round(np.add(h, left_support[2]), 2), left_support[3], right_support[3]

# @Timer("update_states", text="update_particle_states call: {:.5f} seconds", logger=None)
def update_particle_states(model_particles, model_supports, bed_particles):
    """ Set each model particle's current 'active' state.
    
    
    
    If any model particle (pX) has a particle 
    resting on it in the stream then pX must 
    be set to Inactive indicated by a boolean 0.
    
    If pX does not have any particles resting
    on top of it then it is considered Active 
    indicated by a boolean 1.
    
    Note: bed particles are always considered
    Inactive.
    
    
    Keyword arguments:
    model_particles -- model particle list
    bed_particles -- bed particle list
    
    """
    # Set all model particles to active

    model_particles[:,4] = 1
    in_stream_particles = model_particles[model_particles[:,0] != -1]
    # New method, same results as previous. Cannot fully vectorize due to find_supports()
    inactive_left = np.intersect1d(in_stream_particles[:,3], model_supports[:,0])
    inactive_right = np.intersect1d(in_stream_particles[:,3], model_supports[:,1])

    if inactive_left.size != 0:
        model_particles[inactive_left.astype(int), 4] = 0
    if inactive_right.size != 0:
        model_particles[inactive_right.astype(int), 4] = 0

    return model_particles

@njit
# @Timer("# find_supports", text="find_supports call: {:.5f} seconds", logger=None)
def find_supports(particle, model_particles, bed_particles, already_placed):
    """ Find the 2 supporting particles for a given particle.
    
    Provided a particle struct (1-5 array), this function 
    will search the stream for particles that could be 
    considered 'supporting' particles.
    
    More generally, supporting particles are those 
    particles which 'hold up' the particle of concern.
    
    This function can search for supporting particles
    within two scenarios:
        1. The particle of concern is already placed.
        2. The particles is looking to be placed 
        in the stream (i.e after an entrainment event)
    
    Searching for supporting particles at location x could 
    result in two different results depending on the 
    aforementioned scenario, hence the distinct methods.
    
    Keyword arguments:   
    particle -- array representing a particle 
    model_particles -- model particle list
    bed_particles -- bed particle list
    already_placed -- boolean flag indicating if particle has
                      already been placed in the stream, or
                      is looking to be placed
    
    Returns:
    left_support -- the left supporting particle
    right_support -- the right supporting particle
    """ 
    # If particle is already placed in the stream, then supporting particles
    # can only exist below the particle's current elevation (particle[2])
    if already_placed: 
        considered_particles = model_particles[(model_particles[:,2] < particle[2])]
        all_particles = np.concatenate((considered_particles, bed_particles), axis=0)
    # If particle is not yet places (i.e suspended in stream via 
    # entrainment event) then all elevations can be considered for supports.
    else:
        all_particles = np.concatenate((model_particles, bed_particles), axis=0)
       
    # Define location where left and right supporting particles could sit.
    # Note: This limits the model to only using same-sized grains.
    left_center = particle[0] - (particle[1] / 2)
    right_center = particle[0] + (particle[1] / 2)
     
    l_candidates = all_particles[all_particles[:,0] == left_center]
    # try:
    left_support = l_candidates[l_candidates[:,2] 
                                    == np.max(l_candidates[:,2])]
    # except ValueError:
    #     error_msg = (
    #                  f'No left supporting particle found for'
    #                  f'particle {particle[3]}, searched for support at'
    #                  f'{left_center}'
    #     )
    #     logging.error(error_msg)
    #     raise   
        
    r_candidates = all_particles[all_particles[:,0] == right_center] 
    # try:
    right_support = r_candidates[r_candidates[:,2] == np.max(r_candidates[:,2])]
    # except ValueError:
    #     error_msg = (
    #                  f'No right supporting particle found for'
    #                  f'particle {particle[3]}, searched for support at'
    #                  f'{right_center}'
    #     )
    #     logging.error(error_msg)
    #     raise
    return left_support[0], right_support[0]

# @Timer("create_set_modelp", text="set_model_particles call: {:.5f} seconds", logger=None)
def set_model_particles(bed_particles, available_vertices, set_diam, pack_fraction, h):
    """ Create model particle list and set in model stream.
    
    
    
    Create list of n model particles based 
    the packing fraction.
    
    Randomly assign avaliable x-vertices 
    to each model particle. Avaliable
    vertices are derived from the list of
    bed particles. 
    
    The structure of a resulting particle:
        [0] = center coordinate,
        [1] = diameter,
        [2] = elevation,
        [3] = uid,
        [4] = active (boolean)
        [5] = age counter
        [6] = loop age counter
    
    
    Keyword arguments:
    bed_vertices -- list of vertices based on bed particles
    bed_particles -- bed particle list
    
     """ 
    num_placement_loc = np.size(available_vertices)
    # determine the number of model particles that should be introduced into the stream bed
    num_particles = determine_num_particles(pack_fraction, num_placement_loc)
    # create an empty n-6 array to store model particle information
    model_particles = np.zeros([num_particles, 7], dtype='float')
    model_supp = np.zeros([num_particles, 2], dtype='float')
  
    for particle in range(num_particles):  
        
        # the following lines select a vertex to place the current particle at, 
        # and ensure that it is not already occupied by another particle
        random_idx = random.randint(0, np.size(available_vertices)-1)
        vertex = available_vertices[random_idx]
        available_vertices = available_vertices[available_vertices != vertex]

        # intialize the particle information
        model_particles[particle][0] = vertex 
        model_particles[particle][1] = set_diam
        
        model_particles[particle][3] = particle # id number for each particle
        model_particles[particle][4] = 1 # each particle begins as active
        
        # place particle at the chosen vertex
        p_x, p_y, left_supp, right_supp  = place_particle(model_particles[particle], 
                                                            model_particles, 
                                                            bed_particles, 
                                                            h)
            
        model_particles[particle][0] = p_x
        model_particles[particle][2] = p_y
        model_particles[particle][5] = 0
        model_particles[particle][6] = 0

        model_supp[particle][0] = left_supp
        model_supp[particle][1] = right_supp


    # update particle states so that supporting particles are inactive
    model_particles = update_particle_states(model_particles, model_supp, bed_particles)
    
    return model_particles, model_supp

# @Timer("compute_available_vertices", text="compute_avail_vertices call: {:.5f} seconds", logger=None)
def compute_available_vertices(model_particles, bed_particles, set_diam, level_limit,
                               lifted_particles=None):
    """ Compute the avaliable vertices in the model 
    stream.

    Identifies the distinct elevations 
    present in the stream then looks at groups of
    particles in decesnding order of their elevation. 
    
    For each group, if a particle is sitting on a vertex
    x, then x is added to the nulled_vertices array. 
    Then vertices created by two particles in the group
    are considered, where v is the set of such vertices. 
    If v is not already in nulled_vertices, then it is 
    added to the available_vertices.
    
    This ends once the bed particles (lowest elev) have 
    been considered.
    
    
    Keyword arguments: 
        model_particles -- list of model particles
        bed_particles -- list of bed particles
        lifted_particles  -- idx of the 'lifted' particles. Default None
    
    Returns:
        available_vertices -- the set of available vertices
    """
    nulled_vertices = []
    avail_vertices = []
    
    # If we are lifting particles, we need to consider the subset of particles
    # that includes every particles _except_ the particles being 
    if lifted_particles is not None:
        # TODO: Unecessary deepcopy. Refactor to mask or something else.
        model_particles_lifted = copy.deepcopy(model_particles)   
        model_particles_lifted = np.delete(model_particles_lifted, 
                                           lifted_particles, 0)
        all_particles = np.concatenate((model_particles_lifted, 
                                        bed_particles), axis=0)
    else:    
        all_particles = np.concatenate((model_particles, 
                                        bed_particles), axis=0)

    # all_particles = np.concatenate((model_particles, 
    #                                 bed_particles), axis=0)
    # Get unique model particle elevations in stream (descending)
    elevations = elevation_list(all_particles[:,2])
    
    for idx, elevation in enumerate(elevations):
        tmp_particles = all_particles[all_particles[:,2] == elevation]
        
        for particle in tmp_particles:    
            nulled_vertices.append(particle[0])
        
        right_vertices = tmp_particles[:,0] + (set_diam / 2)
        left_vertices = tmp_particles[:,0] - (set_diam / 2)
        tmp_shared_vertices = np.intersect1d(left_vertices, right_vertices)
        
        # Enforce level limit by nulling any vertex above limit:
        if len(elevations) == level_limit+1 and idx==0: 
            for vertex in tmp_shared_vertices:
                nulled_vertices.append(vertex)
        
        for vertex in tmp_shared_vertices:
            if vertex not in nulled_vertices:
                avail_vertices.append(vertex)
                
        del(tmp_shared_vertices)
        del(tmp_particles)
        
    available_vertices = np.array(avail_vertices)
    
    return available_vertices


def elevation_list(elevations, desc=True):
    """ Return a sorted list of unique elevation values """
    ue = np.unique(elevations)
    if desc:
           ue = ue[::-1]
    return ue
  
def compute_hops(event_particle_ids, model_particles, mu, sigma, normal=False):
    """ Given a list of (event) paritcles, this function will 
    add a 'hop' distance to all particles' current x-locations. 
    This value represents the desired hop location of the given 
    event particle during a entrainment event.
    
    
    Hop distances are randomly selected from a log-normal or normal
    distribution. Default is logNormal.
    
    Keyword arguments:
        event_particle_ids -- list of event particle ids
        model_particles -- the model's np arry of model_particles
    
    Returns:
        event_particles -- list of event particles with 'hopped' x-locations
    
    """
    event_particles = model_particles[event_particle_ids]
    if normal:
        s = np.random.normal(mu, sigma, len(event_particle_ids))
    else:
        s = np.random.lognormal(mu, sigma, len(event_particle_ids))
    s_hop = np.round(s, 1)
    s_hop = list(s_hop)
    event_particles[:,0] = event_particles[:,0] + s_hop
    
    return event_particles
 
def move_model_particles(event_particles, model_particles, model_supp, bed_particles, available_vertices, h):
    """ Given an array of event particles, move the event particles
    to next closest valid vertex within the model stream and update 
    model_particle array accordingly.

    Keyword arguments:
        event_particles -- list of event particles (particles being entrained)
        model_particles -- ndarray of model particles
        bed_particles -- ndarray of bed particles
        available_particles -- ndarray of available vertices in the stream
    
    Returns:
        entrainment_dict    -- dictionary of the event particle movements using
                                    (particle_id, entrainment_location) key-value pair
        model_particles     -- updated model particle array 
        updated_avail_vert  -- updated list of available_vertices
        particle_flux       -- number of particles which passed the downstream 
                                    boundary during this event

    """
    entrainment_dict = {}
    # particle_flux = 0
    for particle in event_particles: 
        orig_x = model_particles[model_particles[:,3] == particle[3]][0][0]
        verified_hop = find_closest_vertex(particle[0], available_vertices)
        
        if verified_hop == -1:
            # particle_flux += 1
            exceed_msg = (
                f'Particle {int(particle[3])} exceeded stream...'
                f'sending to -1 axis'
            )
            logging.info(exceed_msg) 
            particle[6] = particle[6] + 1
            particle[0] = verified_hop
            # check which subregion boundaries it crossed
            # crossed the final subregion boundary
        else:
            hop_msg = (
                f'Particle {int(particle[3])} entrained from {orig_x} '
                f'to {verified_hop}. Desired placement was: {particle[0]}'
            )
            logging.info(hop_msg)
            particle[0] = verified_hop
            placed_x, placed_y, left_supp, right_supp = place_particle(particle, model_particles, bed_particles, h)
            particle[0] = placed_x
            particle[2] = placed_y

            model_supp[int(particle[3])][0] = left_supp
            model_supp[int(particle[3])][1] = right_supp

            # crossed 0 or 1 of the n-1 subregion boundaries
            
        entrainment_dict[particle[3]] = verified_hop
        model_particles[model_particles[:,3] == particle[3]] = particle
        
    updated_avail_vert = np.setdiff1d(available_vertices, list(entrainment_dict.values()))
    
    return entrainment_dict, model_particles, model_supp, updated_avail_vert


def update_flux(initial_positions, final_positions, iteration, subregions):
    # This can _most definitely_ be made quicker but for now, it works
    for position in range(0, len(initial_positions)):

        initial_pos = initial_positions[position]
        final_pos = final_positions[position]

        for idx, subregion in enumerate(subregions):
            if (initial_pos >= subregion.leftBoundary()) and (subregion.rightBoundary() > initial_pos):
                start_idx = idx

        for subregion_idx in range(start_idx, len(subregions)):
            if final_pos >= subregions[subregion_idx].rightBoundary():
                subregions[subregion_idx].incrementFlux(iteration)
            elif final_pos == -1 and subregion_idx == len(subregions)-1:
                subregions[subregion_idx].incrementFlux(iteration)

    return subregions

    
def find_closest_vertex(desired_hop, available_vertices):
    """ Find the closest downstream (greater than or equal) vertex
    in availbale vertices. If nothing exists, then return -1.
    
    Keyword arguments:
    desired_hop -- float representing the desired hop location
    available_location -- np array of available vertices in model
    
    Returns:
    vertex -- the closest available vertex that is >= desired_hop
    """    
    available_vertices = np.sort(available_vertices)
    forward_vertices = available_vertices[available_vertices >= desired_hop]
    
    if forward_vertices.size < 1:
        vertex = -1
    else:
        vertex = forward_vertices[0]
    return vertex    


# TODO: confirm the naming of function
# @Timer("check_unique_entrainments", text="check_unique_entrainments call: {:.5f} seconds", logger=None)      
def check_unique_entrainments(entrainment_dict):
    """ Check that all entrainments in the dictionary are unqiue. 
    
    This function will flag any input with non-unique
    entrainments. For a model with n entrainments, and k 
    particles entraining at the same vertex, a list of k-1 particles 
    will returned. The list represents those particles that 
    should be re-entrained (forced to a different vertex).
    
    Keyword arguments:
        entrainment_dict -- dictionary with key=id and value=vertex 
                            particle (id) is being entrained at
                            
    Returns:
        unique_flag -- boolean indicating if entrainment_dict had 
                        only unique entrainments (true) or at 
                        least one non-unique entrainemnt event (false)
        redo_list -- list of particles to be re-entrained in order to
                        achieve uniqueness. If unique_flag is True 
                        then list will be returned empty
    """
    redo_list = []
    unique_flag = True
    # create defaultdict struct to avoid 'Missing Key' errors while grouping
    entrainment_groups = defaultdict(list)
    for p_id, vertex in entrainment_dict.items():
        entrainment_groups[vertex].append(p_id)
    
    entrainment_groups = dict(entrainment_groups)
    for vertex, p_id in entrainment_groups.items():
        if vertex == -1:
            pass
        elif len(p_id) > 1:
            unique_flag = False
            nonunique_msg = (
                f'Non-unique entrainment: The following particles attempted to '
                f'entrain at vertex {vertex}: {p_id}.'
            )
            logging.info(nonunique_msg)
            stay_particle = random.sample(p_id, 1)[0]
            unique_flag = False
            stay_select = (
                f'Randomly selecting {stay_particle} to remain at {vertex}, all ' 
                f'others will be forced to the next available vertex.'
            )
            logging.info(stay_select)
            for particle in p_id:
                if particle != stay_particle:
                    redo_list.append(int(particle))
        
    return unique_flag, redo_list     


def increment_age(model_particles, e_event_ids):
    """"Increment model particles' age, set event particles to age 0"""
    
    model_particles[:,5] = model_particles[:,5] + 1 
    model_particles[e_event_ids, 5] = 0
    
    return model_particles

# End entrainment and model particle related functions
