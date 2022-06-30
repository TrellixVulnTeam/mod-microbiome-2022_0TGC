"""Run training synthetic docker models"""
from __future__ import print_function
import argparse
import getpass
import os
import tarfile
import time

import docker
import synapseclient


def create_log_file(log_filename, log_text=None):
    """Create log file"""
    with open(log_filename, 'w') as log_file:
        if log_text is not None:
            if isinstance(log_text, bytes):
                log_text = log_text.decode("utf-8")
            log_file.write(log_text.encode("ascii", "ignore").decode("ascii"))
        else:
            log_file.write("No Logs")


def store_log_file(syn, log_filename, parentid, store=True):
    """Store log file"""
    statinfo = os.stat(log_filename)
    if statinfo.st_size > 0 and statinfo.st_size/1000.0 <= 50:
        ent = synapseclient.File(log_filename, parent=parentid)
        if store:
            try:
                syn.store(ent)
            except synapseclient.exceptions.SynapseHTTPError as err:
                print(err)


def remove_docker_container(container_name):
    """Remove docker container"""
    client = docker.from_env()
    try:
        cont = client.containers.get(container_name)
        cont.stop()
        cont.remove()
    except Exception:
        print("Unable to remove container")


def remove_docker_image(image_name):
    """Remove docker image"""
    client = docker.from_env()
    try:
        client.images.remove(image_name, force=True)
    except Exception:
        print("Unable to remove image")


def tar(directory, tar_filename):
    """Tar all files in a directory

    Args:
        directory: Directory path to files to tar
        tar_filename:  tar file path
    """
    with tarfile.open(tar_filename, "w") as tar_o:
        tar_o.add(directory)


def untar(directory, tar_filename):
    """Untar a tar file into a directory

    Args:
        directory: Path to directory to untar files
        tar_filename:  tar file path
    """
    with tarfile.open(tar_filename, "r") as tar_o:
        tar_o.extractall(path=directory)


def main(syn, args):
    """Run docker model"""

    if args.status == "INVALID":
        raise Exception("Docker image is invalid")

    client = docker.DockerClient(base_url='unix://var/run/docker.sock')
    config = synapseclient.Synapse().getConfigFile(
        configPath=args.synapse_config
    )
    authen = dict(config.items("authentication"))
    client.login(username=authen['username'],
                 password=authen['password'],
                 registry="https://docker.synapse.org")

    # Get Docker image to run and volumes to be mounted.
    docker_image = args.docker_repository + "@" + args.docker_digest
    output_dir = os.getcwd()
    input_dir = args.input_dir

    print("mounting volumes")
    mounted_volumes = {output_dir: '/output:rw',
                       input_dir: '/input:ro'}

    # Format the mounted volumes so that Docker SDK can understand.
    all_volumes = [output_dir, input_dir]
    volumes = {}
    for vol in all_volumes:
        volumes[vol] = {'bind': mounted_volumes[vol].split(":")[0],
                        'mode': mounted_volumes[vol].split(":")[1]}

    # Look for if the container exists already, if so, reconnect
    print("checking for containers")
    container = None
    errors = None
    for cont in client.containers.list(all=True, ignore_removed=True):
        if args.submissionid in cont.name:
            # Must remove container if the container wasn't killed properly
            if cont.status == "exited":
                cont.remove()
            else:
                container = cont

    # Run the Docker container in detached mode.
    if container is None:
        print("running container")
        try:
            container = client.containers.run(docker_image,
                                              detach=True,
                                              volumes=volumes,
                                              name=args.submissionid,
                                              network_disabled=True,
                                              mem_limit='6g',
                                              stderr=True)
        except docker.errors.APIError as err:
            container = None
            remove_docker_container(args.submissionid)
            errors = str(err) + "\n"
        else:
            errors = ""

    # Create a logfile to catch stdout/stderr from the Docker runs.
    print("creating logfile")
    log_filename = args.submissionid + "_log.txt"
    open(log_filename, 'w').close()

    # While container is running, capture logs every 60s. Remove container
    # when done.
    if container is not None:
        while container in client.containers.list(ignore_removed=True):
            log_text = container.logs()
            create_log_file(log_filename, log_text=log_text)
            store_log_file(syn, log_filename, args.parentid, store=args.store)
            time.sleep(60)

        # Must run again to make sure all the logs are captured.
        log_text = container.logs()
        create_log_file(log_filename, log_text=log_text)
        store_log_file(syn, log_filename, args.parentid, store=args.store)

        container.remove()

    statinfo = os.stat(log_filename)
    if statinfo.st_size == 0 and errors:
        create_log_file(log_filename, log_text=errors)
        store_log_file(syn, log_filename, args.parentid, store=args.store)

    print("finished running Docker model")
    remove_docker_image(docker_image)

    # Check for prediction files once the Docker run is complete.
    output_folder = os.listdir(output_dir)
    if not output_folder:
        raise Exception("No 'predictions.csv' file written to /output, "
                        "please check inference docker")
    elif "predictions.csv" not in output_folder:

        # TODO: remove once live
        if "participant_predictions.csv" in output_folder:
            os.rename("participant_predictions.csv", "predictions.csv")
        else:
            raise Exception("No 'predictions.csv' file written to /output, "
                            "please check inference docker")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--submissionid", required=True,
                        help="Submission Id")
    parser.add_argument("-p", "--docker_repository", required=True,
                        help="Docker Repository")
    parser.add_argument("-d", "--docker_digest", required=True,
                        help="Docker Digest")
    parser.add_argument("-i", "--input_dir", required=True,
                        help="Input Directory")
    parser.add_argument("-c", "--synapse_config", required=True,
                        help="credentials file")
    parser.add_argument("--store", action='store_true',
                        help="to store logs")
    parser.add_argument("--parentid", required=True,
                        help="Parent Id of submitter directory")
    parser.add_argument("--status", required=True, help="Docker image status")
    args = parser.parse_args()
    syn = synapseclient.Synapse(configPath=args.synapse_config)
    syn.login()
    main(syn, args)
