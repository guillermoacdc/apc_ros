#include <string>
#include <iostream>
#include <string>
#include <iostream>
#include <pcl/point_types.h>
#include <pcl/console/time.h>   // TicToc
#include <pcl/registration/icp.h>
#include <pcl/common/transforms.h>
#include <pcl/visualization/cloud_viewer.h>
#include <pcl/visualization/pcl_visualizer.h>
#include <pcl/PointIndices.h>
#include <pcl/conversions.h>
#include <pcl/PCLPointCloud2.h>
#include <pcl_conversions/pcl_conversions.h>
#include <vector>
#include <eigen_conversions/eigen_msg.h>
#include "apc_msgs/GetCloudFrustum.h"
#include "apc_msgs/CullCloudBackground.h"
#include "geometry_msgs/Point.h"
#include "geometry_msgs/Pose.h"
#include "ros/ros.h"
#include "apc_msgs/GetMesh.h"
#include "geometry_msgs/PoseStamped.h"
#include "../src/pcl_tools/pcl_functions.h"
#include "../src/pcl_tools/pcl_tools.h"

std::string stlpath;
class APC_MeshFinder {

public:
    static bool get_mesh(apc_msgs::GetMesh::Request &req, apc_msgs::GetMesh::Response &resp);
    APC_MeshFinder();
    ros::NodeHandle nh;
    ros::ServiceServer service;
};

bool APC_MeshFinder::get_mesh(apc_msgs::GetMesh::Request &req, apc_msgs::GetMesh::Response &resp){
    boost::filesystem::path path_to_mesh(stlpath);
    boost::filesystem::path object_name(req.object_name);
    boost::filesystem::path rest_of_path("reduced_meshes");

    pcl::PCLPointCloud2 cloud;
    bool success;
    std::cout << "Looking for a file at " << (path_to_mesh / object_name /rest_of_path / object_name).string() + ".stl" << std::endl;
    success = pcl_tools::cloud_from_stl((path_to_mesh / object_name / rest_of_path / object_name).string() + ".stl", cloud);
    pcl_conversions::fromPCL(cloud, resp.cloud);
    resp.success = success;
    return true;
}

APC_MeshFinder::APC_MeshFinder(){
    pcl::console::print_highlight ("Initializing Mesh Server\n");
    nh.getParam("stlpath", stlpath);
    service = nh.advertiseService("/get_mesh", get_mesh);
    pcl::console::print_highlight ("--Mesh Server Initialized\n");
}

int main(int argc, char** argv) {
    ros::init(argc, argv, "mesh_node");
    APC_MeshFinder *apc_meshfinder(new APC_MeshFinder());

    ros::spin();
}