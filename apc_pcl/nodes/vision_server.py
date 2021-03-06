#!/usr/bin/python
import rospy
from sensor_msgs.msg import PointCloud2, Image, CameraInfo
from std_msgs.msg import Header
from geometry_msgs.msg import PointStamped, Point, Pose, PoseStamped, Vector3
from apc_msgs.srv import *
from apc_msgs.msg import BinState, ObjectState, DPMObject
import cv2
from cv_bridge import CvBridge, CvBridgeError
import numpy as np
from rospy.numpy_msg import numpy_msg
from apc_tools import (Image_Subscriber, Bin_Segmenter, xyzharray, xyzarray, xyzwarray, pqfrompose, pose_from_matrix,
    make_image_msg, load_background, path_to_root)
import tf
import os
import rosbag


class Vision_Server(object):
    def __init__(self):
        rospy.init_node('vision_server')

        self.simulate_segmentation = rospy.get_param('simulate_segmentation')
        self.train_segmentation = rospy.get_param('train_segmentation')
        self.visualize = rospy.get_param('visualize')
        # Record all of the data necessary to make a fake service call
        self.record_data = rospy.get_param('record')

        ## Image and cloud
        self.im_sub = Image_Subscriber('/kinect2_bottom/depth/cloud_image', self.got_image)        
        self.camera_info_sub = rospy.Subscriber('/kinect2_bottom/rgb_rect/camera_info', numpy_msg(CameraInfo), self.got_camera_info)
        self.camera_matrix = None
        self.image, self.show_image = None, None
        self.segmented = None

        self.cloud_sub = rospy.Subscriber('/kinect2_bottom/depth_highres/points/', PointCloud2, self.got_cloud)
        self.cloud = None

        ## TF
        self.Listener = tf.TransformListener()
        self.Transformer = tf.TransformerROS(True, rospy.Duration(10.0))        

        self.point_pub = rospy.Publisher('test_poses', PoseStamped, queue_size=20)
        self.bin_pub = rospy.Publisher('bin_points', PointStamped, queue_size=40)

        # Service inits
        if self.simulate_segmentation and not self.train_segmentation:
            segmentation_server = 'simulate_segment_image'
        else:
            segmentation_server = 'segment_image'    

        if not self.record_data:
            print "Waiting for segmentation server"
            rospy.wait_for_service(segmentation_server)

        rospy.logwarn("Looking for DPM server {}".format(segmentation_server))
        self.segmentation_proxy = rospy.ServiceProxy(segmentation_server, SegmentImage)
        self.frustum_proxy = rospy.ServiceProxy('cull_frustum', GetCloudFrustum)
        self.background_cull_proxy = rospy.ServiceProxy('cull_background', CullCloudBackground)
        print "Loading background cloud"
        self.backgr_cloud, _, self.backgr_pose = load_background()
        self.background_culled = None

        self.target_cloud_proxy = rospy.ServiceProxy('/get_mesh', GetMesh)

        # Check if we are running lone ICP instead of full SHOT detector
        if rospy.get_param('run_icp'):
            self.registration_proxy = rospy.ServiceProxy('/shot_detector', shot_detector_srv)
        else:
            self.registration_proxy = rospy.ServiceProxy('/apc_object_detection/Shot_detector', shot_detector_srv)

        need_list = [self.image, self.camera_matrix, self.cloud]
        while(((self.image is None) or (self.camera_matrix is None) or (self.cloud is None)) and not rospy.is_shutdown()):
            if self.image is None:
                rospy.loginfo("Have not cached image yet, are you running the cloud to image converter?")
            if self.cloud is None:
                rospy.loginfo("Have not cached cloud yet, are you running the kinect?")
            if self.camera_matrix is None:
                rospy.loginfo("Have not yet found camera matrix")
            rospy.sleep(1)

        self.Bin_Seg = Bin_Segmenter(camera_matrix=self.camera_matrix)
        rospy.Service('run_vision', RunVision, self.run_vision)
        rospy.loginfo('----ready----')

        # Image viewing loop
        if self.visualize:
            self.view_loop()

    def got_image(self, msg):
        self.image = msg

    def got_cloud(self, msg):
        self.cloud = msg

    def got_camera_info(self, msg):
        self.camera_matrix = msg.K.reshape(3, 3)
        if self.record_data:
            self.camera_info = msg
        self.camera_info_sub.unregister()

    def view_loop(self):
        while(not rospy.is_shutdown()):
            if self.show_image is None:
                continue
            cv2.imshow("display", 
                cv2.resize(
                    self.show_image, 
                    (self.show_image.shape[1] // 2, self.show_image.shape[0] // 2)
                )
            )

            if self.segmented is not None:
                cv2.imshow("sub_segment", self.segmented)

            key = cv2.waitKey(10) & 0xff

            if key == ord('q'):
                break

    def publish_pose(self, pose, frame='crichton_origin'):
        print 'publishing a point'
        # point = point[:3]
        self.point_pub.publish(
            header=Header(
                stamp=rospy.Time.now(),
                frame_id=frame,
            ),
            pose=pose,
        )

    def publish_bin_pt(self, point, frame='kinect2_bottom_rgb_optical_frame'):
        point = point[:3]
        self.bin_pub.publish(
            header=Header(
                stamp=rospy.Time.now(),
                frame_id=frame,
            ),
            point=Point(*point),
        )

    def publish_bin(self, _bin, transform=None, transform_frame='crichton_origin'):
        bin_points = self.Bin_Seg.get_bin_points(_bin)
        for bin_world in bin_points:
            # bin_world = np.dot(bin_world_tf, point)
            bin_transformed = np.dot(transform, bin_world)
            self.publish_bin_pt(bin_transformed, frame=transform_frame)

    def find_object(self, object_name, object_list, bin_segmented, _bin, x, y):
        print "Running segmentation for {}".format(_bin.bin_name)
        segmentation_result = self.segmentation_proxy(
            make_image_msg(bin_segmented),
            object_name,
            object_list,
        )

        if segmentation_result.success is False:
            rospy.logwarn("Could not find {} in the image".format(object_name))
            return None

        object_poses = []  # Object poses
        for obj in segmentation_result.found_objects:
            print 'Detected {}, box x: {}, y: {}, width: {}, height: {}'.format(
                object_name, obj.x,
                obj.y, obj.height, obj.width
            )

            cv2.rectangle(
                bin_segmented,
                (obj.x, obj.y),
                (obj.x + obj.width, obj.y + obj.height),
                (0, 255, 0),
                2
            )
            
            self.segmented = bin_segmented

            # If we are in training mode, we are just a proxy for sending the image to matlab
            if self.train_segmentation:
                return None

            print "Requesting background culling"
            if self.background_culled is None:
                self.background_culled = self.background_cull_proxy(self.cloud, self.backgr_cloud, _bin.pose_shelf_frame, self.backgr_pose)
            print "Recieved culled background"

            vector_to_target = self.Bin_Seg.get_unit_vector(
                np.array(
                    [x + obj.x + (obj.width / 2), y + obj.y + (obj.height / 2)]
                )
            )

            print 'Sending image of size {} for frustum culling'.format(self.image.shape)
            object_alone = self.frustum_proxy(
                self.background_culled.cloud,
                make_image_msg(self.image),
                Vector3(*vector_to_target),
                obj.x + x,
                obj.y + y,
                obj.height,
                obj.width,
                not self.simulate_segmentation,
            )
            print 'Got frustum culled cloud'

            print 'Requesting target cloud'
            target_cloud = self.target_cloud_proxy(object_name)
            if not target_cloud.success:
                rospy.logerr("Failed to find .stl mesh for {}".format(object_name))
                return None

            print 'Got target cloud'

            print 'Attempting to register'
            registration = self.registration_proxy(
                object_alone.sub_cloud, 
                target_cloud.cloud,
                object_name
            )
            if not registration.success:
                rospy.logerr("Failed to register {}".format(object_name))
                return None

            print "Got registered object"
            object_pose = registration.pose
            object_poses.append(object_pose)
            break  # Only execute once
            
        return object_poses

    def run_vision(self, srv):
        assert self.image is not None, "Have not yet cached an image"
        self.show_image = np.copy(self.image)

        target_frame = "kinect2_bottom_rgb_optical_frame";
        source_frame = "crichton_origin";
        now = rospy.Time.now()
        rospy.sleep(1.0)
        m = self.Listener.lookupTransform(target_frame, source_frame, now)
        transform = self.Transformer.fromTranslationRotation(*m)

        if self.record_data:
            self.save_bag(srv, transform)
            return RunVisionResponse()

        rospy.loginfo('Running vision')
        info = ""
        info += '\nGot camera ID {}'.format(srv.camera_id)

        bin_states = []
        for number, _bin in enumerate(srv.bins):
            bin_state = BinState(
                bin_name=_bin.bin_name
            )
            object_states = []

            bin_size = xyzarray(_bin.bin_size)
            shelf_position = xyzharray(_bin.pose_shelf_frame.position)
            object_names = [i.object_id for i in _bin.object_list]
            object_name = _bin.target_item

            print "Looking for {} in {}".format(object_name, _bin.bin_name)
            self.publish_bin(_bin, transform=transform, transform_frame=target_frame)
            bin_seg_response = self.Bin_Seg.segment_bin(self.image, _bin, transform)
            self.Bin_Seg.draw_bin(self.show_image, _bin, transform)

            if bin_seg_response is None:
                rospy.logwarn("Bin {} not in full view, skipping".format(_bin.bin_name))
                bin_state.found = False
                bin_states.append(bin_state)
                continue  # The bin is not entirely in the camera view
            bin_segmented, (x, y, w, h) = bin_seg_response

            print "Looking for {}".format(object_name)
            # Core operation -- finding the object, segmenting it, extracting pose
            # This is in the event that we find multiple of one object
            object_poses = self.find_object(object_name, object_names, bin_segmented, _bin, x, y)
            ## ----------
            if object_poses is None:
                print "Could not find object pose"
                object_state = ObjectState(
                    object_id=object_name,
                    object_key='',
                    object_pose=Pose(
                        position=Point(
                            x=1000,
                            y=1000,
                            z=1000,
                        )
                    ),
                    found=False,
                )
                object_states.append(object_state)
                continue

            for object_pose in object_poses:
                self.publish_pose(object_pose, frame='kinect2_bottom_rgb_optical_frame')
                print 'Object pose kinect2_bottom_rgb_optical_frame', object_pose

                pq = pqfrompose(object_pose)  # Position, quaternion
                object_pose_kinect = self.Transformer.fromTranslationRotation(*pq)
                kinect_world_tf = np.linalg.inv(transform)
                object_pose_world = np.dot(kinect_world_tf, object_pose_kinect)  # Matrix
                object_pose_world_msg = pose_from_matrix(object_pose_world)
                print object_pose_world_msg

                object_state = ObjectState(
                    object_id=object_name,
                    object_key='',
                    object_pose=object_pose_world_msg,
                    found=True,
                )
                object_states.append(object_state)
                break  # Hack. Only run this once

            bin_state.object_list = object_states
            bin_states.append(bin_state)

            # Printing stuff
            info += '\n{}: Name: {}'.format(number, _bin.bin_name)
            position = _bin.pose_bin_shelf.position
            size = _bin.bin_size
            info += '\nPosition: ({}, {}, {})\n Size: ({}, {}, {})'.format(
                position.x,
                position.y,
                position.z,
                size.x,
                size.y,
                size.z,
            )
            info += '\nContains:'
            for _object in _bin.object_list:
                info += '\n\tID: {} Key: {}'.format(_object.object_id, _object.object_key)
            else:
                info += '\n\tNothing'

        rospy.loginfo(info)

        # Then call DPM (Or simulated DPM)
        print 'Found {} bins'.format(len(bin_states))
        if len(bin_states) < 12:
            for k in range(len(bin_states), 12):
                bin_states.append(BinState())
        print 'Sending', len(bin_states)

        return RunVisionResponse(
            bin_contents=bin_states
        )

    def save_bag(self, srv, transform):
        store_path = os.path.join(path_to_root(), 'apc_pcl')
        path = os.path.join(store_path, 'data_bag_2.bag')
        print 'Recording session to {}'.format(path)
        bag = rosbag.Bag(path, 'w')
        bag.write('/kinect2_bottom/depth_highres/points/', self.cloud)
        bag.write('/kinect2_bottom/depth/cloud_image', make_image_msg(self.image))
        bag.write('/kinect2_bottom/rgb_rect/camera_info', self.camera_info)
        bag.write('/transform', pose_from_matrix(transform))
        bag.write('service', srv)
        bag.close()


if __name__ == '__main__':
    vs = Vision_Server()
    rospy.spin()