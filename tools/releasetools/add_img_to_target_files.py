#!/usr/bin/env python2
#
# Copyright (C) 2014 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Given a target-files zipfile that does not contain images (ie, does
not have an IMAGES/ top-level subdirectory), produce the images and
add them to the zipfile.

Usage:  add_img_to_target_files [flag] target_files

  -a  (--add_missing)
      Build and add missing images to "IMAGES/". If this option is
      not specified, this script will simply exit when "IMAGES/"
      directory exists in the target file.

  -r  (--rebuild_recovery)
      Rebuild the recovery patch and write it to the system image. Only
      meaningful when system image needs to be rebuilt.

  --replace_verity_private_key
      Replace the private key used for verity signing. (same as the option
      in sign_target_files_apks)

  --replace_verity_public_key
       Replace the certificate (public key) used for verity verification. (same
       as the option in sign_target_files_apks)

  --is_signing
      Skip building & adding the images for "userdata" and "cache" if we
      are signing the target files.

  --verity_signer_path
      Specify the signer path to build verity metadata.
"""

import sys

if sys.hexversion < 0x02070000:
  print >> sys.stderr, "Python 2.7 or newer is required."
  sys.exit(1)

import datetime
import errno
import os
import shutil
import tempfile
import zipfile

import build_image
import common
import sparse_img

OPTIONS = common.OPTIONS

OPTIONS.add_missing = False
OPTIONS.rebuild_recovery = False
OPTIONS.replace_verity_public_key = False
OPTIONS.replace_verity_private_key = False
OPTIONS.is_signing = False
OPTIONS.verity_signer_path = None

def GetCareMap(which, imgname):
  """Generate care_map of system (or vendor) partition"""

  assert which in ("system", "vendor")
  _, blk_device = common.GetTypeAndDevice("/" + which, OPTIONS.info_dict)

  simg = sparse_img.SparseImage(imgname)
  care_map_list = []
  care_map_list.append(blk_device)
  care_map_list.append(simg.care_map.to_string_raw())
  return care_map_list


def AddSystem(output_zip, prefix="IMAGES/", recovery_img=None, boot_img=None):
  """Turn the contents of SYSTEM into a system image and store it in
  output_zip."""

  prebuilt_path = os.path.join(OPTIONS.input_tmp, prefix, "system.img")
  if os.path.exists(prebuilt_path):
    print "system.img already exists in %s, no need to rebuild..." % (prefix,)
    return prebuilt_path

  def output_sink(fn, data):
    ofile = open(os.path.join(OPTIONS.input_tmp, "SYSTEM", fn), "w")
    ofile.write(data)
    ofile.close()

  if OPTIONS.rebuild_recovery:
    print "Building new recovery patch"
    common.MakeRecoveryPatch(OPTIONS.input_tmp, output_sink, recovery_img,
                             boot_img, info_dict=OPTIONS.info_dict)

  block_list = common.MakeTempFile(prefix="system-blocklist-", suffix=".map")
  imgname = BuildSystem(OPTIONS.input_tmp, OPTIONS.info_dict,
                        block_list=block_list)
  common.ZipWrite(output_zip, imgname, prefix + "system.img")
  common.ZipWrite(output_zip, block_list, prefix + "system.map")
  return imgname


def BuildSystem(input_dir, info_dict, block_list=None):
  """Build the (sparse) system image and return the name of a temp
  file containing it."""
  return CreateImage(input_dir, info_dict, "system", block_list=block_list)


def AddSystemOther(output_zip, prefix="IMAGES/"):
  """Turn the contents of SYSTEM_OTHER into a system_other image
  and store it in output_zip."""

  prebuilt_path = os.path.join(OPTIONS.input_tmp, prefix, "system_other.img")
  if os.path.exists(prebuilt_path):
    print "system_other.img already exists in %s, no need to rebuild..." % (prefix,)
    return

  imgname = BuildSystemOther(OPTIONS.input_tmp, OPTIONS.info_dict)
  common.ZipWrite(output_zip, imgname, prefix + "system_other.img")

def BuildSystemOther(input_dir, info_dict):
  """Build the (sparse) system_other image and return the name of a temp
  file containing it."""
  return CreateImage(input_dir, info_dict, "system_other", block_list=None)


def AddVendor(output_zip, prefix="IMAGES/"):
  """Turn the contents of VENDOR into a vendor image and store in it
  output_zip."""

  prebuilt_path = os.path.join(OPTIONS.input_tmp, prefix, "vendor.img")
  if os.path.exists(prebuilt_path):
    print "vendor.img already exists in %s, no need to rebuild..." % (prefix,)
    return prebuilt_path

  block_list = common.MakeTempFile(prefix="vendor-blocklist-", suffix=".map")
  imgname = BuildVendor(OPTIONS.input_tmp, OPTIONS.info_dict,
                        block_list=block_list)
  common.ZipWrite(output_zip, imgname, prefix + "vendor.img")
  common.ZipWrite(output_zip, block_list, prefix + "vendor.map")
  return imgname


def BuildVendor(input_dir, info_dict, block_list=None):
  """Build the (sparse) vendor image and return the name of a temp
  file containing it."""
  return CreateImage(input_dir, info_dict, "vendor", block_list=block_list)

def AddOem(output_zip, prefix="IMAGES/"):
  """Turn the contents of OEM into a oem image and store in it
  output_zip."""

  prebuilt_path = os.path.join(OPTIONS.input_tmp, prefix, "oem.img")
  if os.path.exists(prebuilt_path):
    print "oem.img already exists in %s, no need to rebuild..." % (prefix,)
    return

  block_list = common.MakeTempFile(prefix="oem-blocklist-", suffix=".map")
  imgname = BuildOem(OPTIONS.input_tmp, OPTIONS.info_dict,
                     block_list=block_list)
  with open(imgname, "rb") as f:
    common.ZipWriteStr(output_zip, prefix + "oem.img", f.read())
  with open(block_list, "rb") as f:
    common.ZipWriteStr(output_zip, prefix + "oem.map", f.read())


def BuildOem(input_dir, info_dict, block_list=None):
  """Build the (sparse) oem image and return the name of a temp
  file containing it."""
  return CreateImage(input_dir, info_dict, "oem", block_list=block_list)


def CreateImage(input_dir, info_dict, what, block_list=None):
  print "creating " + what + ".img..."

  img = common.MakeTempFile(prefix=what + "-", suffix=".img")

  # The name of the directory it is making an image out of matters to
  # mkyaffs2image.  It wants "system" but we have a directory named
  # "SYSTEM", so create a symlink.
  try:
    os.symlink(os.path.join(input_dir, what.upper()),
               os.path.join(input_dir, what))
  except OSError as e:
    # bogus error on my mac version?
    #   File "./build/tools/releasetools/img_from_target_files"
    #     os.path.join(OPTIONS.input_tmp, "system"))
    # OSError: [Errno 17] File exists
    if e.errno == errno.EEXIST:
      pass

  image_props = build_image.ImagePropFromGlobalDict(info_dict, what)
  fstab = info_dict["fstab"]
  mount_point = "/" + what
  if fstab and mount_point in fstab:
    image_props["fs_type"] = fstab[mount_point].fs_type

  # Use a fixed timestamp (01/01/2009) when packaging the image.
  # Bug: 24377993
  epoch = datetime.datetime.fromtimestamp(0)
  timestamp = (datetime.datetime(2009, 1, 1) - epoch).total_seconds()
  image_props["timestamp"] = int(timestamp)

  if what == "system":
    fs_config_prefix = ""
  else:
    fs_config_prefix = what + "_"

  fs_config = os.path.join(
      input_dir, "META/" + fs_config_prefix + "filesystem_config.txt")
  if not os.path.exists(fs_config):
    fs_config = None

  # Override values loaded from info_dict.
  if fs_config:
    image_props["fs_config"] = fs_config
  if block_list:
    image_props["block_list"] = block_list

  succ = build_image.BuildImage(os.path.join(input_dir, what),
                                image_props, img)
  assert succ, "build " + what + ".img image failed"

  return img


def AddUserdata(output_zip, prefix="IMAGES/"):
  """Create a userdata image and store it in output_zip.

  In most case we just create and store an empty userdata.img;
  But the invoker can also request to create userdata.img with real
  data from the target files, by setting "userdata_img_with_data=true"
  in OPTIONS.info_dict.
  """

  prebuilt_path = os.path.join(OPTIONS.input_tmp, prefix, "userdata.img")
  if os.path.exists(prebuilt_path):
    print "userdata.img already exists in %s, no need to rebuild..." % (prefix,)
    return

  image_props = build_image.ImagePropFromGlobalDict(OPTIONS.info_dict, "data")
  # We only allow yaffs to have a 0/missing partition_size.
  # Extfs, f2fs must have a size. Skip userdata.img if no size.
  if (not image_props.get("fs_type", "").startswith("yaffs") and
      not image_props.get("partition_size")):
    return

  print "creating userdata.img..."

  # Use a fixed timestamp (01/01/2009) when packaging the image.
  # Bug: 24377993
  epoch = datetime.datetime.fromtimestamp(0)
  timestamp = (datetime.datetime(2009, 1, 1) - epoch).total_seconds()
  image_props["timestamp"] = int(timestamp)

  # The name of the directory it is making an image out of matters to
  # mkyaffs2image.  So we create a temp dir, and within it we create an
  # empty dir named "data", or a symlink to the DATA dir,
  # and build the image from that.
  temp_dir = tempfile.mkdtemp()
  user_dir = os.path.join(temp_dir, "data")
  empty = (OPTIONS.info_dict.get("userdata_img_with_data") != "true")
  if empty:
    # Create an empty dir.
    os.mkdir(user_dir)
  else:
    # Symlink to the DATA dir.
    os.symlink(os.path.join(OPTIONS.input_tmp, "DATA"),
               user_dir)

  img = tempfile.NamedTemporaryFile()

  fstab = OPTIONS.info_dict["fstab"]
  if fstab:
    image_props["fs_type"] = fstab["/data"].fs_type
  succ = build_image.BuildImage(user_dir, image_props, img.name)
  assert succ, "build userdata.img image failed"

  common.CheckSize(img.name, "userdata.img", OPTIONS.info_dict)
  common.ZipWrite(output_zip, img.name, prefix + "userdata.img")
  img.close()
  shutil.rmtree(temp_dir)


def AddUserdataExtra(output_zip, prefix="IMAGES/"):
  """Create extra userdata image and store it in output_zip."""

  image_props = build_image.ImagePropFromGlobalDict(OPTIONS.info_dict,
                                                  "data_extra")

  # The build system has to explicitly request extra userdata.
  if "fs_type" not in image_props:
    return

  extra_name = image_props.get("partition_name", "extra")

  prebuilt_path = os.path.join(OPTIONS.input_tmp, prefix, "userdata_%s.img" % extra_name)
  if os.path.exists(prebuilt_path):
    print "userdata_%s.img already exists in %s, no need to rebuild..." % (extra_name, prefix,)
    return

  # We only allow yaffs to have a 0/missing partition_size.
  # Extfs, f2fs must have a size. Skip userdata_extra.img if no size.
  if (not image_props.get("fs_type", "").startswith("yaffs") and
      not image_props.get("partition_size")):
    return

  print "creating userdata_%s.img..." % extra_name

  # The name of the directory it is making an image out of matters to
  # mkyaffs2image.  So we create a temp dir, and within it we create an
  # empty dir named "data", and build the image from that.
  temp_dir = tempfile.mkdtemp()
  user_dir = os.path.join(temp_dir, "data")
  os.mkdir(user_dir)
  img = tempfile.NamedTemporaryFile()

  fstab = OPTIONS.info_dict["fstab"]
  if fstab:
    image_props["fs_type" ] = fstab["/data"].fs_type
  succ = build_image.BuildImage(user_dir, image_props, img.name)
  assert succ, "build userdata_%s.img image failed" % extra_name

  # Disable size check since this fetches original data partition size
  #common.CheckSize(img.name, "userdata_extra.img", OPTIONS.info_dict)
  output_zip.write(img.name, prefix + "userdata_%s.img" % extra_name)
  img.close()
  os.rmdir(user_dir)
  os.rmdir(temp_dir)


def AddCache(output_zip, prefix="IMAGES/"):
  """Create an empty cache image and store it in output_zip."""

  prebuilt_path = os.path.join(OPTIONS.input_tmp, prefix, "cache.img")
  if os.path.exists(prebuilt_path):
    print "cache.img already exists in %s, no need to rebuild..." % (prefix,)
    return

  image_props = build_image.ImagePropFromGlobalDict(OPTIONS.info_dict, "cache")
  # The build system has to explicitly request for cache.img.
  if "fs_type" not in image_props:
    return

  print "creating cache.img..."

  # Use a fixed timestamp (01/01/2009) when packaging the image.
  # Bug: 24377993
  epoch = datetime.datetime.fromtimestamp(0)
  timestamp = (datetime.datetime(2009, 1, 1) - epoch).total_seconds()
  image_props["timestamp"] = int(timestamp)

  # The name of the directory it is making an image out of matters to
  # mkyaffs2image.  So we create a temp dir, and within it we create an
  # empty dir named "cache", and build the image from that.
  temp_dir = tempfile.mkdtemp()
  user_dir = os.path.join(temp_dir, "cache")
  os.mkdir(user_dir)
  img = tempfile.NamedTemporaryFile()

  fstab = OPTIONS.info_dict["fstab"]
  if fstab:
    image_props["fs_type"] = fstab["/cache"].fs_type
  succ = build_image.BuildImage(user_dir, image_props, img.name)
  assert succ, "build cache.img image failed"

  common.CheckSize(img.name, "cache.img", OPTIONS.info_dict)
  common.ZipWrite(output_zip, img.name, prefix + "cache.img")
  img.close()
  os.rmdir(user_dir)
  os.rmdir(temp_dir)


def AddImagesToTargetFiles(filename):
  OPTIONS.input_tmp, input_zip = common.UnzipTemp(filename)

  if not OPTIONS.add_missing:
    for n in input_zip.namelist():
      if n.startswith("IMAGES/"):
        print "target_files appears to already contain images."
        sys.exit(1)

  try:
    input_zip.getinfo("VENDOR/")
    has_vendor = True
  except KeyError:
    has_vendor = False

  has_system_other = "SYSTEM_OTHER/" in input_zip.namelist()

  try:
    input_zip.getinfo("OEM/")
    has_oem = True
  except KeyError:
    has_oem = False

  OPTIONS.info_dict = common.LoadInfoDict(input_zip, OPTIONS.input_tmp)

  common.ZipClose(input_zip)
  output_zip = zipfile.ZipFile(filename, "a",
                               compression=zipfile.ZIP_DEFLATED)

  has_recovery = (OPTIONS.info_dict.get("no_recovery") != "true")
  use_two_step_recovery = (OPTIONS.info_dict.get("no_two_step_recovery") != "true")

  def banner(s):
    print "\n\n++++ " + s + " ++++\n\n"

  banner("boot")
  prebuilt_path = os.path.join(OPTIONS.input_tmp, "IMAGES", "boot.img")
  boot_image = None
  if os.path.exists(prebuilt_path):
    print "boot.img already exists in IMAGES/, no need to rebuild..."
    if OPTIONS.rebuild_recovery:
      boot_image = common.GetBootableImage(
          "IMAGES/boot.img", "boot.img", OPTIONS.input_tmp, "BOOT")
  else:
    boot_image = common.GetBootableImage(
        "IMAGES/boot.img", "boot.img", OPTIONS.input_tmp, "BOOT")
    if boot_image:
      boot_image.AddToZip(output_zip)

  recovery_image = None
  if has_recovery:
    banner("recovery")
    prebuilt_path = os.path.join(OPTIONS.input_tmp, "IMAGES", "recovery.img")
    if os.path.exists(prebuilt_path):
      print "recovery.img already exists in IMAGES/, no need to rebuild..."
      if OPTIONS.rebuild_recovery:
        recovery_image = common.GetBootableImage(
            "IMAGES/recovery.img", "recovery.img", OPTIONS.input_tmp,
            "RECOVERY")
    else:
      recovery_image = common.GetBootableImage(
          "IMAGES/recovery.img", "recovery.img", OPTIONS.input_tmp, "RECOVERY")
      if recovery_image:
        recovery_image.AddToZip(output_zip)

      if use_two_step_recovery:
        banner("recovery (two-step image)")
        # The special recovery.img for two-step package use.
        recovery_two_step_image = common.GetBootableImage(
            "IMAGES/recovery-two-step.img", "recovery-two-step.img",
            OPTIONS.input_tmp, "RECOVERY", two_step_image=True)
        if recovery_two_step_image:
          recovery_two_step_image.AddToZip(output_zip)

  banner("system")
  system_imgname = AddSystem(output_zip, recovery_img=recovery_image,
                             boot_img=boot_image)
  vendor_imgname = None
  if has_vendor:
    banner("vendor")
    vendor_imgname = AddVendor(output_zip)
  if has_system_other:
    banner("system_other")
    AddSystemOther(output_zip)
  if not OPTIONS.is_signing:
    banner("userdata")
    AddUserdata(output_zip)
    banner("extrauserdata")
    AddUserdataExtra(output_zip)
    banner("cache")
    AddCache(output_zip)
  if has_oem:
    banner("oem")
    AddOem(output_zip)

  # For devices using A/B update, copy over images from RADIO/ to IMAGES/ and
  # make sure we have all the needed images ready under IMAGES/.
  ab_partitions = os.path.join(OPTIONS.input_tmp, "META", "ab_partitions.txt")
  if os.path.exists(ab_partitions):
    with open(ab_partitions, 'r') as f:
      lines = f.readlines()
    # For devices using A/B update, generate care_map for system and vendor
    # partitions (if present), then write this file to target_files package.
    care_map_list = []
    for line in lines:
      if line.strip() == "system" and OPTIONS.info_dict.get(
          "system_verity_block_device", None) is not None:
        assert os.path.exists(system_imgname)
        care_map_list += GetCareMap("system", system_imgname)
      if line.strip() == "vendor" and OPTIONS.info_dict.get(
          "vendor_verity_block_device", None) is not None:
        assert os.path.exists(vendor_imgname)
        care_map_list += GetCareMap("vendor", vendor_imgname)

      img_name = line.strip() + ".img"
      img_radio_path = os.path.join(OPTIONS.input_tmp, "RADIO", img_name)
      if os.path.exists(img_radio_path):
        common.ZipWrite(output_zip, img_radio_path,
                        os.path.join("IMAGES", img_name))

      # Zip spec says: All slashes MUST be forward slashes.
      img_path = 'IMAGES/' + img_name
      assert img_path in output_zip.namelist(), "cannot find " + img_name

    if care_map_list:
      file_path = "META/care_map.txt"
      common.ZipWriteStr(output_zip, file_path, '\n'.join(care_map_list))

  common.ZipClose(output_zip)

def main(argv):
  def option_handler(o, a):
    if o in ("-a", "--add_missing"):
      OPTIONS.add_missing = True
    elif o in ("-r", "--rebuild_recovery",):
      OPTIONS.rebuild_recovery = True
    elif o == "--replace_verity_private_key":
      OPTIONS.replace_verity_private_key = (True, a)
    elif o == "--replace_verity_public_key":
      OPTIONS.replace_verity_public_key = (True, a)
    elif o == "--is_signing":
      OPTIONS.is_signing = True
    elif o == "--verity_signer_path":
      OPTIONS.verity_signer_path = a
    else:
      return False
    return True

  args = common.ParseOptions(
      argv, __doc__, extra_opts="ar",
      extra_long_opts=["add_missing", "rebuild_recovery",
                       "replace_verity_public_key=",
                       "replace_verity_private_key=",
                       "is_signing",
                       "verity_signer_path="],
      extra_option_handler=option_handler)


  if len(args) != 1:
    common.Usage(__doc__)
    sys.exit(1)

  AddImagesToTargetFiles(args[0])
  print "done."

if __name__ == '__main__':
  try:
    common.CloseInheritedPipes()
    main(sys.argv[1:])
  except common.ExternalError as e:
    print
    print "   ERROR: %s" % (e,)
    print
    sys.exit(1)
  finally:
    common.Cleanup()
