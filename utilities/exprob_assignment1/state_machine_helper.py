#!/usr/bin/env python
"""
.. module:: state_machine_helper
	:platform: Unix
	:synopsis: Python module for the Helper of the State Machine
   
.. moduleauthor:: Francesco Ferrazzi <s5262829@studenti.unige.it>

ROS node for the first assignment of the Experimental Robotics course of the Robotics Engineering
Master program. The software architecture allows initializing a helper class for the Final State Machine 
which controls the behavior of a surveillance robot. 
This node allows to have cleaner and more readable code in the state_machine.py node, in fact, every task
called in the previously mentioned code is defined in the current node.

Subscribes to:
	/state/battery_low where the state of the battery is published
	
Service:
	/state/recharge to charge the robot
	/armor_interface_srv to communicate with the ontology
	
Action Service:
	/motion/planner to make the planner create the desired path
	/motion/controller to make the controller follow the desired path
"""

import threading
import random
import rospy
import rospkg
import os
import time
import sys
import actionlib
from threading import Lock
from actionlib import SimpleActionClient

# Import used messages defined within the ROS architecture.
from exprob_assignment1.msg import Point, PlanAction, PlanGoal, ControlAction, ControlGoal
#from exprob_assignment1.msg import *

# Import constant name defined to structure the architecture.
from exprob_assignment1 import architecture_name_mapper as anm

# Import the messages used by services and publishers.
from std_msgs.msg import Bool
from std_srvs.srv import SetBool, SetBoolResponse, SetBoolRequest

# Armor import to work with the ontology
from armor_msgs.srv import ArmorDirective, ArmorDirectiveRequest, ArmorDirectiveResponse
from armor_msgs.srv import ArmorDirectiveList, ArmorDirectiveListRequest, ArmorDirectiveListResponse

# A tag for identifying logs producer.
LOG_TAG = anm.NODE_STATE_MACHINE

# get the file path for rospy_tutorials
rp = rospkg.RosPack()
assignment_path = rp.get_path('exprob_assignment1')

# Define the file path in which the ontology is stored
ONTOLOGY_FILE_PATH = os.path.join(assignment_path, "topological_map", "topological_map.owl")
ONTOLOGY_FILE_PATH_DEBUG = os.path.join(assignment_path, "topological_map", "topological_map_debug.owl")
WEB_PATH = 'http://bnc/exp-rob-lab/2022-23'

# Initialize and define the client to use armor
cli_armorontology = rospy.ServiceProxy('/armor_interface_srv', ArmorDirective) 
# Initialize and define the request message for armor
armorontology_req = ArmorDirectiveRequest()
# Initialize and define the arg list to pass to the ontology
ARGS = []
# Initialize and define the number of rooms, corridors and doors in the environment
NUMBER_ROOMS = 4
NUMBER_CORRIDORS = 3
NUMBER_DOORS = 7
# Initialize and define the time for which a robot checks the room
WAIT_SURVEILLANCE_TIME = rospy.Duration(0.15)
# Define the number for which the state of the action client is done
DONE = 3 # since the get_state() function returns 3 when the action server achieves the goal



def ontology_manager(command, primary_command_spec, secondary_command_spec, ARGS):
	""" 
	Function used to communicate with the ARMOR service to set and retrieve informations of the ontology
	regarding the environment. This function is used instead of the ARMOR API.
		
	Args:
		command: it is the command to execute (e.g. ADD, LOAD, ...).
		primary_command_spec: it is the primary command specification (optional).
		secondary_command_spec: it is the secondary command specification (optional).
		ARGS: it is the list of arguments (e.g. list of individuals to add).

	Returns:
		armorontology_res: it returns a list of queried objects.
		
	"""
	armorontology_req.armor_request.client_name = 'example'
	armorontology_req.armor_request.reference_name = 'ontoRef'
	armorontology_req.armor_request.command = command
	armorontology_req.armor_request.primary_command_spec = primary_command_spec
	armorontology_req.armor_request.secondary_command_spec = secondary_command_spec
	armorontology_req.armor_request.args = ARGS
	rospy.wait_for_service('/armor_interface_srv')
	try:
			armorontology_res = (cli_armorontology(armorontology_req)).armor_response.queried_objects
			return armorontology_res
	except rospy.ServiceException as e:
			print('Service call failed: %s' %e)
			sys.exit(1)
			
 	
def ontology_format(old_list, start, end):
	""" 
	Function that takes as input a list and returns a new one, which starts from the old one.
	The new list takes every element of the old list, starting from the index specified by 'start'
	and finishing at the index specified by 'end'. In this way, only characters and numbers in 
	indexes that are between 'start' and 'end' will be copied into the new list.
		
	Args:
		old_list: is the list that needs to be formatted.
		start: starting list's element index that will be copied to the new list.
		end: ending list's element index that will be copied to the new list.

	Returns:
		new_list: is the correctly formatted list.
		
	"""
	new_list = [num[start:end] for num in old_list]
	return new_list



class Helper:
	"""
	This class is created to decouple the implementation of the Finite State Machine, allowing to have a
	more readable and cleaner code in the state_machine.py node. This class manages the synchronization 
	with subscribers, services and action servers to achieve the correct behavior.
	
	"""
	def __init__(self):
		""" 
		Function that initializes the class Helper.
		
		Args:
			self: instance of the current class.
		
		"""
		# Initialize the variables used in the class 
		self.battery_low = False            # Set to True if the battery of the robot is low
		self.map_completed = False          # Set to True when the ontology is complete
		self.reasoner_done = False          # Set to True after querying the ontology
		self.plan_completed = False         # Set to True when the planner ended the execution
		self.control_completed = False      # Set to True when the controller ended the execution
		self.charge_reached = False         # Set to True when the charging station is reached
		self.check_completed = False        # Set to True when the robot has finished checking the location of arrival
		
		self._rooms = []                         # List of room objects
		self._doors = []                         # List of door objects
		self._corridors = []                     # List of corridor objects
		self._locations = []                     # List to store all the locations e.g. rooms + corridors
		self._viapoints = []                     # List of via points randomically generated by the palnner
		self.prev_loc = anm.INIT_LOCATION        # Previous location, the robot starts from location 'E'
		self.next_loc = ''                       # Future location in which the robot will move, starts empty
		self.charge_loc = anm.CHARGE_LOCATION    # Define the charging location
		self.target_point = Point()              # Initialize the target point for the planner action service
		self.current_point = Point()             # Initialize the current point for the planner action service
		
		# Define the initial position as current position
		self.current_point.x = anm.INIT_POINT[0]
		self.current_point.y = anm.INIT_POINT[1]
		
		# Initialize the current time
		self.timer_now = str(int(time.time()))  
		# Initialize and define the mutex to work with transition variables
		self.mutex = Lock()
		
		# Load the ontology
		ARGS = [ONTOLOGY_FILE_PATH, WEB_PATH, 'true', 'PELLET', 'false']
		ontology_manager('LOAD', 'FILE', '', ARGS)	
		log_msg = f'Loading of the ontology went well'
		rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		
		# Subscribe to the topic that controls the battery level.
		self.battery_sub = rospy.Subscriber(anm.TOPIC_BATTERY_LOW, Bool, self.battery_callback)
		
		# Initialize and define the client for the recharge service
		rospy.wait_for_service(anm.TOPIC_RECHARGE)
		self.recharge_cli = rospy.ServiceProxy(anm.TOPIC_RECHARGE, SetBool)
		
		# Initialize and define the action client for the planner action service
		self.planner_cli = actionlib.SimpleActionClient(anm.ACTION_PLANNER, PlanAction)
		self.planner_cli.wait_for_server()
		
		# Initialize and define the action client for the controller action service
		self.controller_cli = actionlib.SimpleActionClient(anm.ACTION_CONTROLLER, ControlAction)
		self.controller_cli.wait_for_server()
			
		
	def build_environment(self):
		""" 
		Method that initializes the environment ontology using the ARMOR service.
		It creates the desired indoor environment in a random way, based on a fixed number 
		of rooms, doors and corridors. 
		It also communicates with the ontology to initialize and define everything 
		that will be needed to guarantee the correct behavior of the program.
		
		Args:
			self: instance of the current class.
		
		"""
		# Create the room objects
		for a in range(1,NUMBER_ROOMS+1):
			self._rooms.append("R" + str(a))
		# Create the door objects 
		for b in range(1,NUMBER_DOORS+1):
			self._doors.append("D" + str(b))
		# Create the corridor objects
		for c in range(1,NUMBER_CORRIDORS):
			self._corridors.append("C" + str(c))	
		# Randomize the lists
		random.shuffle(self._rooms)
		random.shuffle(self._doors)
		random.shuffle(self._corridors)
		log_msg = f'ROOMS: {self._rooms}'
		rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		log_msg = f'DOORS: {self._doors}'
		rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		# Put one door for every room
		for d in range(0,NUMBER_ROOMS):
			ARGS = ['hasDoor', self._rooms[d], self._doors[d]]
			ontology_manager('ADD', 'OBJECTPROP', 'IND', ARGS)
		# Make the doors of the rooms adjacent to the corridors
		for e in range(0,2):
			ARGS = ['hasDoor', self._corridors[0], self._doors[e]]
			ontology_manager('ADD', 'OBJECTPROP', 'IND', ARGS)
		for f in range(2,4):
			ARGS = ['hasDoor', self._corridors[1], self._doors[f]]
			ontology_manager('ADD', 'OBJECTPROP', 'IND', ARGS)
		# Make corridor C1 and C2 have a door in common
		ARGS = ['hasDoor', self._corridors[0], self._doors[4]]
		ontology_manager('ADD', 'OBJECTPROP', 'IND', ARGS)
		ARGS = ['hasDoor', self._corridors[1], self._doors[4]]
		ontology_manager('ADD', 'OBJECTPROP', 'IND', ARGS)	
		# Adding 'E' to the corridor's list
		self._corridors.append("E")
		log_msg = f'CORRIDORS: {self._corridors}'
		rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		# Put two doors in the corridor 'E' one in common with 'C1' and the other with 'C2'
		ARGS = ['hasDoor', self._corridors[0], self._doors[5]]
		ontology_manager('ADD', 'OBJECTPROP', 'IND', ARGS)
		ARGS = ['hasDoor', self.charge_loc, self._doors[5]]
		ontology_manager('ADD', 'OBJECTPROP', 'IND', ARGS)
		ARGS = ['hasDoor', self._corridors[1], self._doors[6]]
		ontology_manager('ADD', 'OBJECTPROP', 'IND', ARGS)
		ARGS = ['hasDoor', self.charge_loc, self._doors[6]]
		ontology_manager('ADD', 'OBJECTPROP', 'IND', ARGS)
		# Define the locations
		self._locations = self._rooms + self._corridors
		location_number = range(0,NUMBER_ROOMS+NUMBER_CORRIDORS) 
		# Disjoint corridors, rooms and doors
		ARGS = self._rooms + self._corridors + self._doors
		ontology_manager('DISJOINT', 'IND', '', ARGS)
		# State the robot initial position
		ARGS = ['isIn', 'Robot1', self.prev_loc]
		ontology_manager('ADD', 'OBJECTPROP', 'IND' , ARGS)
		# Get a time in the past (before the timestamp of the robot)
		self.timer_now = str(int(1000000000)) # This is done to make every room URGENT at the beginning  
		# Start the timestamp in every location to retrieve when a location becomes urgent
		for g in location_number:
			ARGS = ['visitedAt', self._locations[g], 'Long', self.timer_now]
			ontology_manager('ADD', 'DATAPROP', 'IND', ARGS)
		# Update the timestamp of corridor 'E' since the robot spawns in it
		ARGS = ['']
		ontology_manager('REASON', '', '', ARGS)
		ARGS = ['visitedAt', self.charge_loc]
		last_location = ontology_manager('QUERY', 'DATAPROP', 'IND', ARGS)
		last_location = ontology_format(last_location, 1, 11) 
		self.timer_now = str(int(time.time())) # initial location is not urgent
		ARGS = ['visitedAt', self.charge_loc, 'Long', self.timer_now, last_location[0]]
		ontology_manager('REPLACE', 'DATAPROP', 'IND', ARGS)
		# Save ontology for DEBUG purposes
		#ARGS = [ONTOLOGY_FILE_PATH_DEBUG] # <--- uncomment this line for ontology debug
		#ontology_manager('SAVE', '', '', ARGS) # <--- uncomment this line for ontology debug
		log_msg = f'The map has been generated in the ontology\n\n'
		rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		self.map_completed = True   # Set to True only the one involved in the state
		
		
	def world_done(self):
		""" 
		Get the value of the variable responsible for stating the creation of the environment 
		using the ARMOR service to define the ontology.
		The returning value will be `True` if the map was created, `False` otherwise.
		
		Args:
			self: instance of the current class.
		
		Returns:
			self.map_completed: Bool value that states the status of the generation of the map.
		
		"""
		return self.map_completed
		
			
	def reason(self):
		""" 
		Method that communicates with the ontology already created to retrieve information
		and decide, based on the desired pre-determined behavior, where the robot should
		move next.
		First of all, reachable rooms and their status (e.g. ROOM, URGENT, CORRIDOR) are retrieved.
		Then, each reachable room is checked and the robot will move first in URGENT locations.
		If there are no URGENT locations, it stays on CORRIDORS. If there are no CORRIDORS the robot
		moves to a random ROOM. In the end, the next location that will be visited is returned.
		
		Args:
			self: instance of the current class.
			
		Returns:
			self.next_loc: is the next location that will be reached decided by the reasoner.
		
		"""
		# Reset the boolean variables
		self.reset_var()
		log_msg = f'The Robot is in location: {self.prev_loc}'
		rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		# Reason about the onoloy
		ARGS = ['']
		ontology_manager('REASON', '', '', ARGS)
		# Retreive the locations that the robot can reach
		ARGS = ['canReach', 'Robot1']
		can_reach = ontology_manager('QUERY', 'OBJECTPROP', 'IND', ARGS)
		can_reach = ontology_format(can_reach, 32, -1)
		random.shuffle(can_reach) # Make the choice randomic
		log_msg = f'The Robot can reach: {can_reach}'
		rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		# Retrieve the status of the reachable locations
		loc_status = []
		all_status = []
		for loc in range(0, len(can_reach)):
			ARGS = [can_reach[loc], 'false']
			loc_status = ontology_manager('QUERY', 'CLASS', 'IND', ARGS)  
			loc_status = ontology_format(loc_status, 32, -1)
			all_status.append(loc_status)
		log_msg = f'Status of the locations: {all_status}'
		rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		# Check the status of the room (e.g. ROOM, CORRIDOR, URGENT)
		urgent_loc = []
		possible_corridor = []
		for sta in range(0, len(all_status)):
			for urg in range(0, len(all_status[sta])):
				# If location is urgent and it is reachable
				if all_status[sta][urg] == 'URGENT':
					urgent_loc.append(can_reach[sta])
				# If location is a corridor and it is reachable
				elif all_status[sta][urg] == 'CORRIDOR':
					possible_corridor.append(can_reach[sta])
		# Retrieve the next location taht will be checked by the robot
		if len(urgent_loc) == 0:
			log_msg = f'There are no urgent locations'
			rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
			if len(possible_corridor) == 0:
				log_msg = f'There are no reachable corridors'
				rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
				self.next_loc = can_reach # take the first randomic reachable room
			else:
				log_msg = f'The reachable corridors are: {possible_corridor}'
				rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
				self.next_loc = possible_corridor # take the first reachable corridor
		else:
			log_msg = f'The Urgent locations are: {urgent_loc}'
			rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
			self.next_loc = urgent_loc # take the first randomic urgent room
		if type(self.next_loc) == list:
			self.next_loc = self.next_loc[0]
		self.reasoner_done = True   # Set to True only the one involved in the state
		return self.next_loc
		
		
	def reason_done(self):
		""" 
		Get the value of the variable responsible for stating the completion of the reasoning
		phase achieved using the ARMOR service to retrieve informations from the ontology.
		The returning value will be `True` if the reasoner is done, `False` otherwise.
		
		Args:
			self: instance of the current class.
		
		Returns:
			self.reasoner_done: Bool value that states the status of the reasoner.
		
		"""
		return self.reasoner_done
	
	
	def go_to_charge(self):
		""" 
		Function that allows the robot to go to the charging location before it starts the 
		charging routine.
		When the robot's battery is low, it gets as target location the charging station
		and moves towards it. After calling the planner and the controller to reach the location,
		once 'E' is reached, the variable charge_reached is set to True and the robot is ready
		to be charged. 
		
		Args:
			self: instance of the current class.
			
		
		"""
		# Reset the boolean variables
		self.reset_var()
		# Set the next location to be the charging station
		self.next_loc = self.charge_loc
		log_msg = f'Battery of the robot low, next location will be: {self.next_loc}'
		rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		self.planner()
		while self.planner_cli.get_state() != DONE: # Loops until the plan action service is Not DONE
			self.charge_reached = False # Wate time
		log_msg = f'The PLANNER has found the path for the charging station'
		rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		# Get the waypoints that will be used in the Controller
		self._viapoints = (self.planner_cli.get_result()).via_points
		self.controller()
		while self.controller_cli.get_state() != DONE: # Loops until the control action service is Not DONE
			self.charge_reached = False # Wate time
		self.check_controller()
		self.charge_reached = True   # Set to True only the one involved in the state
		
		
	def charge_ready(self):
		""" 
		Get the value of the variable responsible for stating that the robot is ready to be
		charged once the location 'E' is reached.
		The returning value will be `True` if the charge location is reached, `False` otherwise.
		
		Args:
			self: instance of the current class.
		
		Returns:
			self.charge_reached: Bool value that states if the charging location is reached.
		
		"""
		return self.charge_reached
		
	
	def battery_callback(self, msg):
		""" 
		It is the callback that manages the subscriber to the topic: /state/battery_low to retrieve
		the state of the battery.
		
		Args:
			self: instance of the current class.
			msg: is the subscriber to the topic /state/battery_low to get the state of the battery.
		
		"""
		self.mutex.acquire()    # take the mutex
		try: 
			self.battery_low = msg.data    # change the flag of battery low with the received message
			if self.battery_low == True:
				log_msg = f'\n@@@ Battery of the robot is low! Recharging needed @@@\n'
				rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
			if self.battery_low == False:
				log_msg = f'\n@@@ Battery of the robot is full! @@@\n'
				rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		finally:
			self.mutex.release()    # release the mutex
	
			
	def ret_battery_low(self):
		""" 
		Get the value of the variable responsible for stating the power level of the battery
		of the robot. 
		The returning value will be `True` if the battery is low, `False` otherwise.
		
		Args:
			self: instance of the current class.
		
		Returns:
			self.battery_low: Bool value that states the status of the battery.
		
		"""
		return self.battery_low
			
			
	def recharge_srv(self):
		""" 
		Blocking service used to charge the battery of the robot. Once the battery is low 
		and the robot is in the charging location, a request is sent to the service which 
		charges the battery after a defined time and gets a result as soon as it is charged. 
		When the service is done, the battery of the robot is set to high by putting the variable
		battery_low to False.
		
		Args:
			self: instance of the current class.
		
		"""
		request = SetBoolRequest()
		request.data = True
		response = self.recharge_cli(request)
		log_msg = f'The Robot has been recharged! Ready for action!!\n\n'
		rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		self.battery_low = False
	
		
	def planner(self):
		""" 
		This method executes a planner for a surveillance task. It starts by deciding a random
		point inside the environment that will be reached. Then, a request to the PlanGoal() action 
		service is done to simulate a planner.
		
		Args:
			self: instance of the current class.
		
		"""
		# Reset the boolean variables
		self.reset_var()
		request = PlanGoal()
		# Generate a randomic random point 
		self.target_point.x = random.uniform(0, anm.ENVIRONMENT_SIZE[0])
		self.target_point.y = random.uniform(0, anm.ENVIRONMENT_SIZE[1])
		# Define the request for the Planner
		request.target = self.target_point
		request.current = self.current_point
		# Sends the goal to the action server.
		self.planner_cli.send_goal(request)
		
	
	def check_planner(self):
		""" 
		This method checks if the planner has done its execution when the state of the action service
		is equal to DONE.
		When it is done, the random path of via points going from the current point to the target point
		is retrieved. This path is then used by the controller.
		
		Args:
			self: instance of the current class.
		
		"""
		# Execute only when the plan action service is done
		if self.planner_cli.get_state() == DONE:
			log_msg = f'The PLANNER has found the path to get to the target\n\n'
			rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
			# Get the waypoints that will be used in the Controller
			self._viapoints = (self.planner_cli.get_result()).via_points
			self.plan_completed = True  # Set to True only the one involved in the state
	
		
	def plan_done(self):
		""" 
		Get the value of the variable responsible of stating the status of the planner.
		The returning value will be `True` if the planner has finished, `False` otherwise.
		
		Args:
			self: instance of the current class.
		
		Returns:
			self.plan_completed: Bool value that states the status of the planner.
		
		"""
		return self.plan_completed
		
	
	def controller(self):
		""" 
		This function executes the controller for a surveillance task. It starts by 
		getting the via points from the planner and sends a request to the ControlGoal() 
		action service in order to follow the desired path. 
		
		Args:
			self: instance of the current class.
		
		"""
		# Reset the boolean variables
		self.reset_var()
		request = ControlGoal()
		# Define the request for the Controller
		request.via_points = self._viapoints
		# Sends the goal to the action server.
		self.controller_cli.send_goal(request)
		
		
	def check_controller(self):
		""" 
		This method checks if the controller has done its execution when the state of the action 
		service is equal to DONE.
		When the target point is reached, the result is sent back to the client which updates 
		the current point with the target point that was previously sent to the action server.
		Later on, the controller communicates with the ontology to update some information 
		regarding the location in which the robot has arrived, the timestamp of the last motion 
		of the robot and the timestamp of the location that the robot has reached.
		
		Args:
			self: instance of the current class.
		
		"""
		# Execute only when the plan action service is done
		if self.controller_cli.get_state() == DONE:
			log_msg = f'The CONTROLLER has reached the target point'
			rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
			# Get the final destination when arrived and update the current position of the robot
			self.current_point = (self.controller_cli.get_result()).reached_point
			# Update the position of the robot in the ontology
			ARGS = ['isIn', 'Robot1', self.next_loc, self.prev_loc]
			ontology_manager('REPLACE', 'OBJECTPROP', 'IND' , ARGS)
			self.prev_loc = self.next_loc
			log_msg = f'The robot arrived at location: {self.next_loc}\n\n'
			rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
			# Reason about the onoloy
			ARGS = ['']
			ontology_manager('REASON', '', '', ARGS)
			# Retreive the last time the robot moved
			ARGS = ['now', 'Robot1']
			last_motion = ontology_manager('QUERY', 'DATAPROP', 'IND', ARGS)
			last_motion = ontology_format(last_motion, 1, 11)
			# Retreive the last time a specific location has been visited
			ARGS = ['visitedAt', self.next_loc]
			last_location = ontology_manager('QUERY', 'DATAPROP', 'IND', ARGS)
			last_location = ontology_format(last_location, 1, 11) 
			# Update the time
			self.timer_now = str(int(time.time())) 
			# Update the timestamp since the robot moved
			ARGS = ['now', 'Robot1', 'Long', self.timer_now, last_motion[0]]
			ontology_manager('REPLACE', 'DATAPROP', 'IND', ARGS)
			# Update the timestamp since the robot visited the location
			ARGS = ['visitedAt', self.next_loc, 'Long', self.timer_now, last_location[0]]
			ontology_manager('REPLACE', 'DATAPROP', 'IND', ARGS)
			self.control_completed = True  # Set to True only the one involved in the state
	
		
	def control_done(self):
		""" 
		Get the value of the variable responsible of stating the status of the controller.
		The returning value will be `True` if the controller has finished, `False` otherwise.
		
		Args:
			self: instance of the current class.
		
		Returns:
			self.control_completed: Bool value that states the status of the controller.
		
		"""
		return self.control_completed
				
				
	def reset_var(self):
		""" 
		It is used to reset all the variables used to decide when a task has finished its execution.
		
		Args:
			self: instance of the current class.
			
		"""
		self.mutex.acquire()    # take the mutex
		self.reasoner_done = False
		self.plan_completed = False
		self.control_completed = False
		self.charge_reached = False
		self.check_completed = False
		self.mutex.release()    # release the mutex
		
	
	def do_surveillance(self):
		""" 
		It simulates a survaillance task of the location in which the robot arrives when the 
		controller has done its execution. 
		While it explores the location, also the status of the battery is checked.
		
		
		Args:
			self: instance of the current class.
		
		"""
		# Reset the boolean variables
		self.reset_var()
		# Surveillance task, lasts 5 seconds if the battery is charged
		surv_count = 0
		log_msg = f'The robot is surveilling the location'
		rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		while self.battery_low == False and surv_count < 20: # If bettery low there won't be surveillance task
			rospy.sleep(WAIT_SURVEILLANCE_TIME)
			surv_count = surv_count + 1
		if self.battery_low == False:
			log_msg = f'The robot checked location: {self.next_loc}\n\n'
			rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		else:
			log_msg = f'Stop surveilling! Go to the charge station\n\n'
			rospy.loginfo(anm.tag_log(log_msg, LOG_TAG))
		self.check_completed = True  # Set to True only the one involved in the state
	
	
	
	def surveillance_done(self):
		""" 
		Get the value of the variable responsible of stating the status of the surveillance task.
		The returning value will be `True` if the robot has checked the location, `False` otherwise.
		
		Args:
			self: instance of the current class.
		
		Returns:
			self.check_completed: Bool value that states the status of the surveillance task.
		
		"""
		return self.check_completed
		
