# coding: utf-8

from __future__ import division, print_function, unicode_literals, absolute_import

import json
import os
import six

from monty.json import MontyEncoder

from fireworks import FireTaskBase, FWAction
from fireworks.utilities.fw_serializers import DATETIME_HANDLER
from fireworks.utilities.fw_utilities import explicit_serialize
from matgendb.util import get_settings

from matmethods.utils.utils import env_chk
from matmethods.vasp.drones import VaspDrone
from matmethods.vasp.database import MMDb
from matmethods.utils.utils import get_logger

__author__ = 'Anubhav Jain, Kiran Mathew, Shyam Dwaraknath'
__email__ = 'ajain@lbl.gov, kmathew@lbl.gov, shyamd@lbl.gov'

logger = get_logger(__name__)


@explicit_serialize
class VaspToDbTask(FireTaskBase):
    """
    Enter a VASP run into the database. By default, the VASP directory is
    assumed to be the current directory.

    Optional params:
        calc_dir (str): path to dir (on current filesystem) that contains VASP
            output files. Default: use current working directory.
        calc_loc (str OR bool): if True will set most recent calc_loc. If str
            search for the most recent calc_loc with the matching name
        parse_dos (bool): whether to parse the DOS and store in GridFS.
            Defaults to False.
        bandstructure_mode (str): Set to "uniform" for uniform band structure.
            Set to "line" for line mode. If not set, band structure will not
            be parsed.
        additional_fields (dict): dict of additional fields to add
        db_file (str): path to file containing the database credentials.
            Supports env_chk. Default: write data to JSON file.
        build_db (bool): whether to build the indices or not.
        db_indices (list): list of db indices to be built.
    """
    optional_params = ["calc_dir", "calc_loc", "parse_dos",
                       "bandstructure_mode", "additional_fields", "db_file", "build_db",
                       "db_indices"]

    def run_task(self, fw_spec):
        # get the directory that contains the VASP dir to parse
        calc_dir = os.getcwd()
        if "calc_dir" in self:
            calc_dir = self["calc_dir"]
        elif self.get("calc_loc"):
            if isinstance(self["calc_loc"], six.string_types):
                for doc in reversed(fw_spec["calc_locs"]):
                    if doc["name"] == self["calc_loc_name"]:
                        calc_dir = doc["path"]
                        break
            else:
                calc_dir = fw_spec["calc_locs"][-1]["path"]
        # parse the VASP directory
        logger.info("PARSING DIRECTORY: {}".format(calc_dir))
        # get the database connection
        db_file = env_chk(self.get('db_file'), fw_spec)

        task_doc = None

        drone = VaspDrone(additional_fields=self.get("additional_fields"),
                          parse_dos=self.get("parse_dos", False), compress_dos=1,
                          bandstructure_mode=self.get("bandstructure_mode", False), compress_bs=1)
        # assimilate
        task_doc = drone.assimilate(calc_dir)
        # db insertion
        if not db_file:
            with open("task.json", "w") as f:
                f.write(json.dumps(task_doc, default=DATETIME_HANDLER))
        else:
            db_config = get_settings(db_file)
            db = MMDb(host=db_config["host"], port=db_config["port"], database=db_config["database"],
                      user=db_config.get("admin_user"), password=db_config.get("admin_password"),
                      collection=db_config["collection"])
            # build the indices
            if self.get("build_db", False):
                if self.get("db_indices"):
                    db.build(indices=self.get("db_indices"))
            # insert dos/bandstructure to GridFS and update the task document
            if self.get("parse_dos", False) and "calcs_reversed" in task_doc:
                if "dos" in task_doc["calcs_reversed"][0]:
                    dos = json.dumps(task_doc["calcs_reversed"][0]["dos"], cls=MontyEncoder)
                    gfs_id, compression_type = db.insert_gridfs(dos, "dos_fs")
                    task_doc["calcs_reversed"][0]["dos_compression"] = compression_type
                    task_doc["calcs_reversed"][0]["dos_fs_id"] = gfs_id
                    del task_doc["calcs_reversed"][0]["dos"]
            if self.get("bandstructure_mode", False) and "calcs_reversed" in task_doc:
                if "bandstructure" in task_doc["calcs_reversed"][0]:
                    bs = json.dumps(task_doc["calcs_reversed"][0]["bandstructure"], cls=MontyEncoder)
                    gfs_id, compression_type = db.insert_gridfs(bs, "bandstructure_fs")
                    task_doc["calcs_reversed"][0]["bandstructure_compression"] = compression_type
                    task_doc["calcs_reversed"][0]["bandstructure_fs_id"] = gfs_id
                    del task_doc["calcs_reversed"][0]["bandstructure"]
            # insert the task document
            t_id = db.insert(task_doc)
            logger.info("Finished parsing with task_id: {}".format(t_id))
        return FWAction(stored_data={"task_id": task_doc.get("task_id", None)},
                        defuse_children=(task_doc["state"] != "successful"))


@explicit_serialize
class ToDbTask(FireTaskBase):
    """
    Enter data from a calculation into the database.
    Utilizes a drone to parse the current directory into the DB file to insert.


    Required params:
        drone (AbstractDrone): Drone to convert the data to dict


    Optional params:
        db_file (str): path to file containing the database credentials. Supports env_chk. Default: write data to JSON file.
        calc_dir (str): path to dir (on current filesystem) that contains calculation output files. Default: use current working directory.
        calc_loc (str OR bool): if True will set most recent calc_loc. If str search for the most recent calc_loc with the matching name
        options (dict): dict of options to pass into the Drone
        additional_fields (dict): dict of additional fields to add
    """

    required_params = ["drone"]
    optional_params = ["db_file", "calc_dir", "calc_loc", "additional_fields", "options"]

    def run_task(self, fw_spec):
        # get the directory that contains the dir to parse
        calc_dir = os.getcwd()
        if "calc_dir" in self:
            calc_dir = self["calc_dir"]
        elif self.get("calc_loc"):
            if isinstance(self["calc_loc"], six.string_types):
                for doc in reversed(fw_spec["calc_locs"]):
                    if doc["name"] == self["calc_loc_name"]:
                        calc_dir = doc["path"]
                        break
            else:
                calc_dir = fw_spec["calc_locs"][-1]["path"]

        # parse the calc directory
        logger.info("PARSING DIRECTORY: {} USING DRONE: {}".format(
            calc_dir, self['drone'].__class__.__name__))
        # get the database connection
        db_file = env_chk(self.get('db_file'), fw_spec)

        task_doc = None

        drone = self['drone'].__class__()
        task_doc = drone.assimilate(calc_dir)
        if not db_file:
            with open("task.json", "w") as f:
                f.write(json.dumps(task_doc, default=DATETIME_HANDLER))
        else:
            db_config = get_settings(db_file)
            db = MMDb(host=db_config["host"], port=db_config["port"],
                      database=db_config["database"],
                      user=db_config.get("admin_user"), password=db_config.get("admin_password"),
                      collection=db_config["collection"])
            # insert the task document
            t_id = db.insert(task_doc)
            logger.info("Finished parsing with task_id: {}".format(t_id))

        return FWAction(stored_data={"task_id": task_doc.get("task_id", None)},
                        defuse_children=(task_doc["state"] != "successful"))
