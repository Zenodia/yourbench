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
import argparse


if __name__ == '__main__':
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="my_example.yaml")
    parser.add_argument("--name", default=None)
    args = parser.parse_args()
    print("using config yml file = ", args.config)
    config=load_config(args.config)
    stage_config = _load_stage_config(config)
    if args.name :
        
        f_name = args.name 
        print("using user preferred custom naming =", f_name)
    else:
    
        pdf_file_dir=config["pipeline"]["ingestion"]["source_documents_dir"]
        files=[f for f in os.listdir(pdf_file_dir) if f.endswith(".pdf")]
        f_name='_'.join(files) 
        print("using original pdf file name extracted from yaml file=", f_name)

    chunked_dataset = load_from_disk(dataset_path="./example/data/local_saved/English/chunked")
    summarized_dataset = load_from_disk(dataset_path="./example/data/local_saved/English/summarized")
    single_shot_questions = load_from_disk(dataset_path="./example/data/local_saved/English/single_shot_questions")
    single_shot_qs=single_shot_questions.to_pandas()
    single_shot_qs.to_csv(f"/workspace/example/data/csv/{f_name}.csv", index=False)
    
    summarized_dataset.to_csv(f"/workspace/example/data/csv/summary_{f_name}.csv", index=False)
    print(f"saving summary file summary_{f_name}.csv to /workspace/example/data/csv/ folder successfully !")
    print(f"saving file {f_name}.csv to /workspace/example/data/csv/ folder successfully !")