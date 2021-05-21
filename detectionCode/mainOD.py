#!/usr/bin/env python
"""
 Copyright (c) 2018 Intel Corporation

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""
from __future__ import print_function
import sys
import os
from argparse import ArgumentParser, SUPPRESS
import cv2
import numpy as np
import logging as log
from PIL import Image
from PIL.ExifTags import TAGS
from openvino.inference_engine import IECore
from gpiozero import MotionSensor
from picamera import PiCamera
import time
import datetime
#import ngraph as ng


def inference():
    log.basicConfig(format="[ %(levelname)s ] %(message)s", level=log.INFO, stream=sys.stdout)
    
    log.info("Loading Inference Engine")
    ie = IECore()

    # ---1. Read a model in OpenVINO Intermediate Representation (.xml and .bin files) or ONNX (.onnx file) format ---
    model = "/home/pi/ssdincnew/fp16/frozen_inference_graph.xml"
    log.info(f"Loading network:\n\t{model}")
    net = ie.read_network(model=model)
    #func = ng.function_from_cnn(net)
    #ops = func.get_ordered_ops()
    # -----------------------------------------------------------------------------------------------------

    # ------------- 2. Load Plugin for inference engine and extensions library if specified --------------
    log.info("Device info:")
    versions = ie.get_versions("MYRIAD")
    print("{}{}".format(" " * 8, "MYRIAD"))
    print("{}MKLDNNPlugin version ......... {}.{}".format(" " * 8, versions["MYRIAD"].major,
                                                          versions["MYRIAD"].minor))
    print("{}Build ........... {}".format(" " * 8, versions["MYRIAD"].build_number))


    # -----------------------------------------------------------------------------------------------------

    # --------------------------- 3. Read and preprocess input --------------------------------------------

    print("inputs number: " + str(len(net.input_info.keys())))

    for input_key in net.input_info:
        print("input shape: " + str(net.input_info[input_key].input_data.shape))
        print("input key: " + input_key)
        if len(net.input_info[input_key].input_data.layout) == 4:
            n, c, h, w = net.input_info[input_key].input_data.shape

    with os.scandir("/home/pi/Pictures/cameraImages/") as folder:
        for file in folder:
            if file.name.endswith(".jpg") and file.is_file():

                file_path = "/home/pi/Pictures/cameraImages/" + file.name
                images = np.ndarray(shape=(n, c, h, w))
                images_hw = []
                for i in range(n):
                    image = cv2.imread(file_path)
                    ih, iw = image.shape[:-1]
                    images_hw.append((ih, iw))
                    log.info("File was added: ")
                    log.info("        {}".format(file.name))
                    if (ih, iw) != (h, w):
                        log.warning("Image {} is resized from {} to {}".format(file.name, image.shape[:-1], (h, w)))
                        image = cv2.resize(image, (w, h))
                    image = image.transpose((2, 0, 1))  # Change data layout from HWC to CHW
                    images[i] = image

                # -----------------------------------------------------------------------------------------------------

                # --------------------------- 4. Configure input & output ---------------------------------------------
                # --------------------------- Prepare input blobs -----------------------------------------------------
                log.info("Preparing input blobs")
                assert (len(net.input_info.keys()) == 1 or len(
                    net.input_info.keys()) == 2), "Sample supports topologies only with 1 or 2 inputs"
                out_blob = next(iter(net.outputs))
                input_name, input_info_name = "", ""

                for input_key in net.input_info:
                    if len(net.input_info[input_key].layout) == 4:
                        input_name = input_key
                        log.info("Batch size is {}".format(net.batch_size))
                        net.input_info[input_key].precision = 'U8'
                    elif len(net.input_info[input_key].layout) == 2:
                        input_info_name = input_key
                        net.input_info[input_key].precision = 'FP32'
                        if net.input_info[input_key].input_data.shape[1] != 3 and net.input_info[input_key].input_data.shape[1] != 6 or \
                            net.input_info[input_key].input_data.shape[0] != 1:
                            log.error('Invalid input info. Should be 3 or 6 values length.')

                data = {}
                data[input_name] = images

                if input_info_name != "":
                    infos = np.ndarray(shape=(n, c), dtype=float)
                    for i in range(n):
                        infos[i, 0] = h
                        infos[i, 1] = w
                        infos[i, 2] = 1.0
                    data[input_info_name] = infos

                # --------------------------- Prepare output blobs ----------------------------------------------------
                log.info('Preparing output blobs')

                output_name, output_info = "", net.outputs[next(iter(net.outputs.keys()))]
                #output_ops = {op.friendly_name : op for op in ops \
                #              if op.friendly_name in net.outputs and op.get_type_name() == "DetectionOutput"}
                #if len(output_ops) != 0:
                    
                #    output_name, output_info = output_ops.popitem()
                #    print(output_info)
                #else: print("not needed")

                if output_name == "":
                    log.error("Can't find a DetectionOutput layer in the topology")
                    print(output_info)
                    

                output_dims = output_info.shape
                print(output_dims)
                if len(output_dims) != 4:
                    log.error("Incorrect output dimensions for SSD model")
                max_proposal_count, object_size = output_dims[2], output_dims[3]

                if object_size != 7:
                    log.error("Output item should have 7 as a last dimension")

                output_info.precision = "FP32"
                # -----------------------------------------------------------------------------------------------------

                # --------------------------- Performing inference ----------------------------------------------------
                log.info("Loading model to the device")
                exec_net = ie.load_network(network=net, device_name="MYRIAD")
                log.info("Creating infer request and starting inference")
                res = exec_net.infer(inputs=data)
                # -----------------------------------------------------------------------------------------------------

                # --------------------------- Read and postprocess output ---------------------------------------------
                log.info("Processing output blobs")
                res = res[out_blob]
                boxes, classes = {}, {}
                data = res[0][0]
                negative = True
                for number, proposal in enumerate(data):
                    if proposal[2] > 0:
                        imid = np.int(proposal[0])
                        ih, iw = images_hw[imid]
                        label = np.int(proposal[1])
                        confidence = proposal[2]
                        xmin = np.int(iw * proposal[3])
                        ymin = np.int(ih * proposal[4])
                        xmax = np.int(iw * proposal[5])
                        ymax = np.int(ih * proposal[6])
                        print("[{},{}] element, prob = {:.6}    ({},{})-({},{}) batch id : {}" \
                              .format(number, label, confidence, xmin, ymin, xmax, ymax, imid), end="")
                        if proposal[2] > 0.5:
                            print(" WILL BE PRINTED!")
                            negative = False
                            if not imid in boxes.keys():
                                boxes[imid] = []
                            boxes[imid].append([xmin, ymin, xmax, ymax])
                            if not imid in classes.keys():
                                classes[imid] = []
                            classes[imid].append(label)
                        else:
                            
                            print()


                if negative == False:
                    for imid in classes:
                        tmp_image = cv2.imread(file_path)
                        for box in boxes[imid]:
                            cv2.rectangle(tmp_image, (box[0], box[1]), (box[2], box[3]), (232, 35, 244), 2)
                        out_file_path = "/home/pi/Pictures/Detection/" + file.name
                        cv2.imwrite(out_file_path, tmp_image)
                        log.info("Positive detection image created!")
                        #metaextract(file_path)

                if negative == True:
                    tmp_image = cv2.imread(file_path)
                    out_file_path = "/home/pi/Pictures/noDetection/" + file.name
                    cv2.imwrite(out_file_path, tmp_image)
                    log.info("Negative detection image result created!")

                try:
                    os.remove(file_path)
                except: pass

                #metaextract(file_path)
		
    # -----------------------------------------------------------------------------------------------------

    log.info("Execution successful\n")
    log.info(
        "This sample is an API example, for any performance measurements please use the dedicated benchmark_app tool")

def main():
    pir = MotionSensor(4)
    
    while True:
        pir.wait_for_motion()
        camera = PiCamera()
        for i in range(2):
            date = datetime.datetime.now()
            fname = date.strftime("%y%m%d%H%M%S.jpg")
            camera.capture("/home/pi/Pictures/cameraImages/" + fname)
            time.sleep(1)
        camera.close()
        inference()

if __name__ == '__main__':
    sys.exit(main() or 0)
