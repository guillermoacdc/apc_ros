#!/usr/bin/env python
#
# Copyright (c) 2015, Georgia Tech Research Corporation
# All rights reserved.
#
# Author(s): Eric Huang <ehuang@gatech.edu>
# Georgia Tech Humanoid Robotics Lab
# Under Direction of Prof. Andrea Thomaz <athomaz@cc.gatech.edu>
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#     * Redistributions of source code must retain the above
#       copyright notice, this list of conditions and the following
#       disclaimer.
#     * Redistributions in binary form must reproduce the above
#       copyright notice, this list of conditions and the following
#       disclaimer in the documentation and/or other materials
#       provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# ''AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
# OF THE POSSIBILITY OF SUCH DAMAGE.

import numpy as np
import openravepy
import trajoptpy
import trajoptpy.math_utils as mu
from IPython.core.debugger import Tracer
from .action import *



def print_item_collision_checker_pairs(item, cc, env):
    """
    Print out the allowed collision pairs between kinbody and the
    rest of the environment for a given collision checker.

    """
    for item_link in item.GetLinks():
        item_link_name = item_link.GetName()
        for target_body in env.GetBodies():
            for target_link in target_body.GetLinks():
                if cc.CanCollide(item_link, target_link):
                    print item_link_name, ":", target_body.GetName()
                    break


def print_item_collision_pairs(item, problem, env):
    """
    Print out the allowed collision pairs between kinbody and the
    rest of the environment for a given collision checker.

    """
    collision_checkers = problem.GetCollisionCheckers()
    # for cc in collision_checkers:
    #     print_item_collision_checker_pairs(item, cc, env)
    print_item_collision_checker_pairs(item, collision_checkers[0], env)


def set_target_item_collision_properties(action, problem, env):
    """
    This function sets collision checking for the target item to
    enabled or disabled depending on whether the action is a transit, pre-
    grasp, grasp, post-grasp, or nonprehensile.

    """
    # If the robot is holding the object, plan with the grabbed object.
    if is_action_grasping(action):
        robot = env.GetRobot('crichton')
        item = env.GetKinBody(action.object_key)
        link  = robot.GetLink(action.attached_link_id)
        robot.Grab(item, link)
    else:
        robot = env.GetRobot('crichton')
        robot.ReleaseAllGrabbed()
    # If the action is a grasp or postgrasp, disable collisions between the
    # grabbed object and the robot.
    if is_action_pregrasp(action) or is_action_grasp(action) or is_action_postgrasp(action):
        robot = env.GetRobot('crichton')
        item = env.GetKinBody(action.object_key)
        collision_checkers = problem.GetCollisionCheckers()
        for cc in collision_checkers:
            if not item:
                Tracer()()
            for item_link in item.GetLinks():
                # print "Disabling collisions between", item_link.GetName(), "and crichton"
                for robot_link in robot.GetLinks():
                    cc.ExcludeCollisionPair(item_link, robot_link)
                    cc.ExcludeCollisionPair(robot_link, item_link)
                for item_link2 in item.GetLinks():
                    cc.ExcludeCollisionPair(item_link2, item_link)
                    cc.ExcludeCollisionPair(item_link, item_link2)


def reset_target_item_collision_properties(action, problem, env):
    """
    This function resets collision checking for the target item.

    """
    # Otherwise, enable collisions between the item and the robot and itself.
    if is_action_grasp(action) or is_action_postgrasp(action):
        robot = env.GetRobot('crichton')
        item = env.GetKinBody(action.object_key)
        collision_checkers = problem.GetCollisionCheckers()
        for cc in collision_checkers:
            for item_link in item.GetLinks():
                # print "Disabling collisions between", item_link.GetName(), "and crichton"
                for robot_link in robot.GetLinks():
                    cc.IncludeCollisionPair(item_link, robot_link)
                    cc.IncludeCollisionPair(robot_link, item_link)
                for item_link2 in item.GetLinks():
                    cc.IncludeCollisionPair(item_link2, item_link)
                    cc.IncludeCollisionPair(item_link, item_link2)


def check_for_collisions_interp(action, problem, result, env, dn=10):
    """
    Verify that the interpolated trajectory generated by trajopt is collision
    free. Argument 'dn' controls how fine the step sizes between points is.

    """
    # If the action is a pre-grasp, then we do not check the last segment for
    # collisions.
    pregrasp_off = 0
    if is_action_pregrasp(action):
        pregrasp_off = 1
    # Disable collisions for items if the action is a pre-grasp. Grasp
    # and post-grasp actions have attached the object to the robot, so
    # collisions are not reported.
    if is_action_pregrasp(action):
        item = env.GetKinBody(action.object_key)
        item.Enable(False)
    # Check interpolated trajectory for collisions.
    robot = env.GetRobot("crichton")
    T = result.GetTraj()
    num_pts = T.shape[0]
    num_dof = T.shape[1]
    report = openravepy.CollisionReport()
    for i in range(num_pts - 1 - pregrasp_off):
        T_i = T[i:i+1,:]
        T_i = mu.interp2d(np.linspace(0,1,dn), np.linspace(0,1,len(T_i)), T_i)
        for (_,dofs) in enumerate(T_i):
            robot.SetActiveDOFValues(dofs)
            for robot_link in robot.GetLinks():
                collision = env.CheckCollision(robot_link, report)
                if collision:
                    print "collision:", report
                    return False
    # Re-enable collisions for items if the action is a pre-grasp. Grasp
    # and post-grasp actions have attached the object to the robot, so
    # collisions are not reported.
    if is_action_pregrasp(action):
        item = env.GetKinBody(action.object_key)
        item.Enable(True)
    return True


def check_for_collisions_bullet(action, problem, result, env):
    """
    Verify that the trajectory generated by trajopt is collision free.

    """
    robot = env.GetRobot("crichton")
    collision_checkers = problem.GetCollisionCheckers()
    for cc in collision_checkers:
        collisions = cc.BodyVsAll(robot)
        if len(collisions) > 0:
            return False
    return True
