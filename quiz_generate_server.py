from fastmcp import FastMCP
from dotenv import load_dotenv
from colorama import Fore
import asyncio
import subprocess

import sys, os
from yourbench.pipeline.single_shot_question_generation import _load_stage_config
import random
from typing import Any
from dataclasses import field, dataclass

from loguru import logger

from datasets import Dataset
from yourbench.utils.prompts import (
    QUESTION_GENERATION_USER_PROMPT,
    QUESTION_GENERATION_SYSTEM_PROMPT,
    QUESTION_GENERATION_SYSTEM_PROMPT_MULTI,
)
from yourbench.utils.dataset_engine import (
    custom_load_dataset,
    custom_save_dataset,
)

# Import the unified parsing function
from yourbench.utils.parsing_engine import shuffle_mcq, parse_qa_pairs_from_response
from yourbench.utils.inference_engine import InferenceCall, run_inference
from yourbench.utils.loading_engine import load_config
from datasets import Dataset, DatasetDict, load_dataset, load_from_disk, concatenate_datasets
import yaml
from colorama import Fore
load_dotenv()


mcp = FastMCP("MCPTools")
@mcp.tool()
def quiz_generating_pipeline(pdf_file_dir, save_csv_dir):
    """ activate the pipeline to generate the quiz questions and save them to csv files
    
    Args:
        pdf_file_dir (str): path to the folder where the pdf file(s) is/are located
        save_csv_dir (str): path to the folder where you want to save the output csv files
    Returns:
        str: message of whether the pipeline execution successfully or not"""
    
    ## taking a template yaml file, read it in, modify it and save to a new file called test.yaml

    print(Fore.BLUE +"pdf_file_dir =", pdf_file_dir , '\n', "save_csv_dir=", save_csv_dir, Fore.RESET)
    config_file="./my_example.yaml"
    config=load_config(config_file)        
    config["pipeline"]["ingestion"]["source_documents_dir"]= pdf_file_dir 
    os.makedirs(os.path.join(save_csv_dir, "output_dir"),exist_ok=True)
    os.makedirs(os.path.join(save_csv_dir, "processed"),exist_ok=True)
    os.makedirs(os.path.join(save_csv_dir, "csv"),exist_ok=True)
    config["hf_configuration"]["local_dataset_dir"]=os.path.join(save_csv_dir, "output_dir")
    config["pipeline"]["ingestion"]["output_dir"]= os.path.join(save_csv_dir, "processed")
    config["pipeline"]["upload_ingest_to_hub"]["source_documents_dir"]= os.path.join(save_csv_dir, "processed")
        
    yaml_f=open("/workspace/test.yaml","w")
    yaml.dump(config, yaml_f)

    # invoking the pipeline 
    # add logfile for standard output , later on we will pipe this into the UI
    proc =  subprocess.Popen(["yourbench", "run", "--config=./test.yaml"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if save_csv_dir.endswith('/'):
        log_file_path=save_csv_dir + 'logfile.txt'
    else:
        log_file_path=save_csv_dir + '/logfile.txt'
    logfile = open(log_file_path, 'bw')
    while True:
        byte = proc.stdout.read(1)
        if byte:
            sys.stdout.buffer.write(byte)
            sys.stdout.flush()
            logfile.write(byte)
            logfile.flush()
        else:
            break
    exit_status = proc.returncode
    
    pdf_file_dir=config["pipeline"]["ingestion"]["source_documents_dir"]
    f_name=save_csv_dir.split('/')[-1].replace('.pdf','')
    
    print(Fore.CYAN +"using original pdf file name extracted from yaml file=", f_name , Fore.RESET)    
    load_dir=config["hf_configuration"]["local_dataset_dir"]
    chunked_dataset = load_from_disk(dataset_path=os.path.join(load_dir,"chunked"))
    summarized_dataset = load_from_disk(dataset_path=os.path.join(load_dir,"summarized"))
    single_shot_questions = load_from_disk(dataset_path=os.path.join(load_dir,"single_shot_questions"))
    single_shot_qs=single_shot_questions.to_pandas()
    save_csv_path=os.path.join(save_csv_dir, 'csv')
    single_shot_file_save_to = os.path.join(save_csv_path, f"{f_name}.csv")
    print(f"saving file {f_name}.csv to :\n {single_shot_file_save_to} successfully !")
    single_shot_qs.to_csv(single_shot_file_save_to, index=False)
    summary_file_save_to=os.path.join(save_csv_path, f"summary_{f_name}.csv")
    summarized_dataset.to_csv(summary_file_save_to, index=False)
    print(f"saving summary file summary_{f_name}.csv to : \n {summary_file_save_to} successfully !")
    
    
    output_message=f"pdf_file:{f_name}.pdf|single_shot_csv_file:{single_shot_file_save_to}|summary_file:{summary_file_save_to}"
    return output_message


mcp.run(transport="streamable-http",
        host="127.0.0.1",
        port=4777,
        log_level="debug",
        )
