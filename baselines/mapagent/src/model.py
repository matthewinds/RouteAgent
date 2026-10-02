import os
import re
import sys
import json
import openai
import codecs
from pathlib import Path

# add the parent directory to the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utilities import *
from answer_extraction import extract_answer_choice
# from parallel_function_call_qwen import *
from parallel_function_implementation import *
from demos import prompt_policy, prompt_kr, prompt_sg, prompt_qg, prompt_gm, prompt_im

# OpenAI
openai.api_key = os.getenv("OPENAI_API_KEY")

# OpenAI
bing_api_key = os.getenv("BING_API_KEY")


class solver:

    def __init__(self, args):
        # arguments
        for key, value in vars(args).items():
            setattr(self, key, value)
        # for chameleon
        if self.model == "chameleon":
            self.use_caption = False  # disabled by default, could be enabled by the policy

        # external arguments
        self.api_key = openai.api_key
        self.examples, self.pids = self.load_data()

    def load_data(self):
        # load test data
        pid_splits = json.load(open(os.path.join(self.data_root, "pid_splits.json")))
        _examples = json.load(open(file=os.path.join(self.data_root, "problems.json"), mode="r", encoding="utf-8"))

        examples = {pid: _examples[pid] for pid in pid_splits[self.test_split]}
        pids = list(examples.keys())

        # update metadata
        for pid, example in examples.items():
            image = example.get("image_path") or ""
            if not image:
                legacy_image = example.get("image") or ""
                if Path(legacy_image).suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}:
                    image = legacy_image
            if image:
                image_path = Path(image)
                candidates = [
                    image_path,
                    Path(self.data_root) / image_path,
                    Path(self.data_root).parent / image_path,
                    Path(__file__).resolve().parents[1] / "datasets_dir" / "img_data" / image_path,
                    Path(__file__).resolve().parents[1] / "datasets_original" / "MapEval-Visual" / image_path,
                ]
                resolved = next((path.resolve() for path in candidates if path.is_file()), None)
                if resolved is None:
                    raise FileNotFoundError(f"Image for PID {pid} not found: {image}")
                example["image_file"] = str(resolved)
            else:
                example["image_file"] = ""

        # limit the number of test examples
        if self.test_number > 0:
            if self.test_number < len(pids):
                pids = pids[:self.test_number]
                examples = {key: value for key, value in examples.items() if key in pids}

        # load caption data
        if os.path.exists(self.caption_file):
            captions = json.load(open(self.caption_file))["captions"]
            for pid in examples:
                examples[pid]['caption'] = captions[pid] if pid in captions else ""

        # load ocr data
        if os.path.exists(self.ocr_file):
            ocrs = json.load(open(self.ocr_file))["texts"]
            for pid in examples:
                examples[pid]['ocr'] = ocrs[pid] if pid in ocrs else []

        return examples, pids

    def get_question_text(self):
        if "question_text" in self.cache:
            return self.cache["question_text"]

            # context
        text_context = self.cache["example"]["hint"]
        image_context = self.cache["example"]["caption"] if self.use_caption else ""
        context = " ".join([text_context, image_context]).strip()

        # option
        choices = self.cache["example"]["choices"]
        inds = ["A", "B", "C", "D", "E"]
        choice_list = [f"({inds[i]}) {choices[i]}" for i in range(len(choices))]
        option = " ".join(choice_list)

        # question text
        question = self.cache["example"]["question"]
        if context != "":
            question_text = f"{question}\n\nContext: {context}\n\nOptions: {option}"
        else:
            question_text = f"{question}\n\nOptions: {option}"
        # question_text = question
        self.cache["question_text"] = question_text
        return question_text

    def get_metadata(self):
        if "metadata" in self.cache:
            return self.cache["metadata"]

            # extract metadata
        metadata = {}
        example = self.cache["example"]
        # metadata["has_image"] = True if example["image"] else False
        # metadata["grade"] = int(example["grade"].replace("grade", ""))
        # metadata["subject"] = example["subject"]
        # metadata["topic"] = example["topic"]
        # metadata["category"] = example["category"]
        metadata["skill"] = example["skill"]
        metadata["solution"] = example["solution"]
        metadata["img_path"] = example.get("image_file", "")

        self.cache["metadata"] = metadata
        return metadata

    def build_prompt_for_policy(self):
        # get the example
        question_text = self.get_question_text()
        metadata = self.get_metadata()

        # build the prompt
        demo_prompt = prompt_policy.prompt.strip()  # demo prompt
        test_prompt = f"Question: {question_text}\n\nMetadata: {metadata}\n\nModules: "  # test prompt
        full_prompt = demo_prompt + "\n\n" + test_prompt  # full prompt

        return test_prompt, full_prompt

    def predict_modules(self):
        # get the module input
        test_prompt, full_prompt = self.build_prompt_for_policy()
        messages = [
            {"role": "user", "content": full_prompt},
        ]

        # execute the module
        modules = get_chat_response(messages, self.api_key, self.policy_engine, self.policy_temperature,
                                    self.policy_max_tokens)
        # modules = get_qwen_response(messages, self.api_key, self.policy_engine, self.policy_temperature,
        #                             self.policy_max_tokens)

        modules = self.update_modules(modules)

        # update the cache
        self.cache["modules:input"] = test_prompt
        self.cache["modules:output"] = modules
        return modules

    def update_modules(self, _modules):
        # default modules
        default_modules = ["solution_generator", "answer_generator"]

        try:
            modules = eval(_modules.lower().strip())
            assert modules[-2:] == default_modules
        except:
            modules = default_modules

        return modules

    def visual_place_recognizer(self):
        metadata = self.get_metadata()
        query_prompt = prompt_im.prompt_chameleon_img.strip()
        img_path = metadata["img_path"]
        if not img_path:
            raise ValueError("visual_place_recognizer requires an image")
        place_name = get_vision_response(
            img_path,
            query_prompt,
            model=self.sg_engine,
            temperature=self.sg_temperature,
            max_tokens=self.sg_max_tokens,
        )

        refusal_markers = ("can't help", "cannot help", "unable to help", "sorry")
        if not place_name or any(marker in place_name.casefold() for marker in refusal_markers):
            place_name = get_vision_response(
                img_path,
                "Transcribe only the most prominent visible city, district, landmark, or road label "
                "at the center of this map screenshot, followed by the visible map scale. Do not "
                "infer a precise address or identify a private person's location. Return only the "
                "visible label and scale.",
                model=self.sg_engine,
                temperature=self.sg_temperature,
                max_tokens=128,
            )
        if not place_name or any(marker in place_name.casefold() for marker in refusal_markers):
            place_name = "unavailable"

        place_name = f"The location name is {place_name}"
        self.cache["response"] = place_name
        self.cache["visual_place_recognizer:input"] = img_path
        self.cache["visual_place_recognizer:output"] = place_name
        return img_path, place_name

    def image_captioner(self):
        # get the module input
        image_file = self.cache["example"]["image_file"]
        response = self.cache["response"] if "response" in self.cache else ""

        # excute the module 
        if "caption" in self.cache["example"]:
            caption = self.cache["example"]["caption"]
        else:
            if not os.path.exists(image_file):
                caption = ""
            else:
                # TODO: run the image captioner model on the fly
                caption = ""

                # update the response cache
        if caption != "":
            response += f"\n\nImage caption: {caption}"
            response = response.strip()

        # update the cache
        self.cache["response"] = response
        self.cache["image_captioner:input"] = image_file
        self.cache["image_captioner:output"] = caption
        return image_file, caption

    def text_detector(self):
        # get the module input
        image_file = self.cache["example"]["image_file"]
        response = self.cache["response"] if "response" in self.cache else ""

        # excute the module 
        texts = []
        if "ocr" in self.cache["example"]:
            try:
                ocr = eval(self.cache["example"]["ocr"])
                if len(ocr) > 0:
                    texts = [(t[0], t[1]) for t in ocr]  # (coordinates, text)
            except:
                pass
        else:
            if not os.path.exists(image_file):
                texts = []
            else:
                # TODO: run the image captioner model on the fly
                texts = []

        # update the response cache
        if len(texts) > 0:
            response += f"\n\nDetected text in the image: {texts}"
            response = response.strip()

        # update the cache
        self.cache["response"] = response
        self.cache["text_detector:input"] = image_file
        self.cache["text_detector:output"] = texts
        return image_file, texts

    def knowledge_retrieval(self):
        # get the example
        question_text = self.get_question_text()
        metadata = self.get_metadata()
        response = self.cache["response"] if "response" in self.cache else ""

        # build the prompt
        demo_prompt = prompt_kr.prompt.strip()  # demo prompt
        if response != "":
            test_prompt = f"Question: {question_text}\n\nMetadata: {metadata}\n\n{response}\n\nKnowledge:\n"
        else:
            test_prompt = f"Question: {question_text}\n\nMetadata: {metadata}\n\nKnowledge:\n"  # test prompt
        full_prompt = demo_prompt + "\n\n" + test_prompt  # full prompt

        messages = [
            {"role": "user", "content": full_prompt},
        ]

        # execute the module
        knowledge = get_chat_response(messages, self.api_key, self.kr_engine, self.kr_temperature, self.kr_max_tokens)

        # update the response cache
        if knowledge != "" and knowledge != None:
            response += f"\n\nKnowledge:\n{knowledge}"
            response = response.strip()

        # update the cache
        self.cache["response"] = response
        self.cache["knowledge_retrieval:input"] = test_prompt
        self.cache["knowledge_retrieval:output"] = knowledge
        return test_prompt, knowledge

    def query_generator(self):
        # get the example
        question_text = self.get_question_text()
        metadata = self.get_metadata()
        response = self.cache["response"] if "response" in self.cache else ""

        # demo prompt
        demo_prompt = prompt_qg.prompt.strip()
        # test prompt
        if response != "":
            test_prompt = f"Question: {question_text}\n\nMetadata: {metadata}\n\n{response}\n\nSearch Query: "
        else:
            test_prompt = f"Question: {question_text}\n\nMetadata: {metadata}\n\nSearch Query: "
        # full prompt
        full_prompt = demo_prompt + "\n\n" + test_prompt
        messages = [
            {"role": "user", "content": full_prompt},
        ]

        # execute the module
        query = get_chat_response(messages, self.api_key, self.qg_engine, self.qg_temperature, self.qg_max_tokens)
        if query == "" or query == None:
            query = None

        # update the cache
        self.cache["query"] = query
        self.cache["query_generator:input"] = test_prompt
        self.cache["query_generator:output"] = query
        return test_prompt, query

    def bing_search(self):
        # get the module input
        endpoint = self.endpoint
        count = self.search_count
        query = self.cache["query"] if "query" in self.cache else None
        response = self.cache["response"] if "response" in self.cache else ""

        # excute the module (call the Bing Search API and get the responses)
        if query != None and query != "":
            result = call_bing_search(endpoint, bing_api_key, query, count)
        else:
            result = None
        responses = parse_bing_result(result)

        if len(responses) > 0 and responses[0] != "":
            response += f"\n\nBing search response: {responses}"
            response = response.strip()

        # update the cache
        self.cache["response"] = response
        self.cache["bing_search:input"] = query
        self.cache["bing_search:output"] = responses
        return query, responses

    def google_maps(self):
        # get the example
        question_text = self.get_question_text()
        visual_context = self.cache.get("response", "")
        map_query = question_text
        if visual_context:
            map_query += f"\n\nLocation inferred from the supplied map image: {visual_context}"
            map_query += ("\nUse only the visible location. If the image context includes a map scale or "
                          "radius, pass it as radius_km after converting meters to kilometers. "
                          "Never append a state or country that is not shown or stated.")
        extract_information = run_conversation(map_query)

        # update the cache
        self.cache["response"] = extract_information
        self.cache["query_generator:input"] = map_query
        self.cache["query_generator:output"] = extract_information
        return map_query, extract_information

    def build_prompt_for_sg_chameleon(self):
        # get the input
        question_text = self.get_question_text()
        metadata = self.get_metadata()
        response = self.cache["response"] if "response" in self.cache else ""

        # build the prompt
        demo_prompt = prompt_sg.prompt_chameleon.strip()  # WARNING: this is the prompt for chameleon
        if response != "":
            test_prompt = f"Question: {question_text}\n\nMetadata: {metadata}\n\n{response}\n\nSolution: "
        else:
            test_prompt = f"Question: {question_text}\n\nMetadata: {metadata}\n\nSolution: "
        full_prompt = demo_prompt + "\n\n" + test_prompt  # full prompt
        if self.cache["example"].get("image_file"):
            visual_rule = (
                "\n\nImportant MapEval-Visual rule: inspect the attached map screenshot yourself. "
                "If the question asks how many items are shown, visible, depicted, or identifiable "
                "in the image, count only the matching labels/icons visible inside the screenshot. "
                "Do not count the number of Google Maps API candidates. Treat live API results as "
                "supplementary evidence for names, routes, and place details, because they may cover "
                "a different area or time."
            )
            full_prompt += visual_rule
        return test_prompt, full_prompt

    def build_prompt_for_sequencer_chameleon(self):
        # get the input
        question_text = self.get_question_text()
        metadata = self.get_metadata()
        response = self.cache["response"] if "response" in self.cache else ""

        # build the prompt
        demo_prompt = prompt_sg.prompt_chameleon.strip()  # WARNING: this is the prompt for chameleon
        if response != "":
            test_prompt = f"Question: {question_text}\n\nMetadata: {metadata}\n\n{response}\n\nSolution: "
        else:
            test_prompt = f"Question: {question_text}\n\nMetadata: {metadata}\n\nSolution: "
        full_prompt = demo_prompt + "\n\n" + test_prompt  # full prompt
        return test_prompt, full_prompt

    def build_prompt_for_sg_cot(self):
        question = self.cache["example"]["question"]
        choices = self.cache["example"]["choices"]
        hint = self.cache["example"]["hint"]
        caption = self.cache["example"]["caption"]

        # demo prompt
        demo_prompt = prompt_sg.prompt_cot.strip()  # WARNING: this is the prompt for COT

        # option
        inds = ["A", "B", "C", "D", "E"]
        choice_list = [f"({inds[i]}) {choices[i]}" for i in range(len(choices))]
        option = " ".join(choice_list)

        # context
        context = hint.strip()
        if self.use_caption and caption != "":
            context += f" Image: {caption}"

        #  test prompt
        if context != "":
            test_prompt = f"Question: {question}\n\nContext: {context}\n\nOptions: {option}\n\nSolution: "
        else:
            test_prompt = f"Question: {question}\n\nOptions: {option}\n\nSolution: "

        full_prompt = demo_prompt + "\n\n" + test_prompt
        return test_prompt, full_prompt

    def construct_message(self, agents, question, idx):

        # Use introspection in the case in which there are no other agents.
        if len(agents) == 0:
            return {"role": "user",
                    "content": "Can you verify that your answer is correct. Please reiterate your answer, making sure to state your answer at the end of the response."}

        prefix_string = "These are the recent/updated opinions from other agents: "

        for agent in agents:
            agent_response = agent[idx]["content"]
            response = "\n\n One agent response: ```{}```".format(agent_response)

            prefix_string = prefix_string + response

        prefix_string = prefix_string + f"\n\n Use these opinions carefully as additional advice, can you provide an updated answer? Make sure to state your answer at the end of the response:"
        return {"role": "user", "content": prefix_string}

    def construct_assistant_message(self, content):
        return {"role": "assistant", "content": content}

    def most_frequent(self, List):
        counter = 0
        num = List[0]

        for i in List:
            current_frequency = List.count(i)
            if current_frequency > counter:
                counter = current_frequency
                num = i
        return num

    def sequencer(self):
        # get the module input
        if self.model == "chameleon":
            test_prompt, full_prompt = self.build_prompt_for_sg_chameleon()
        else:
            test_prompt, full_prompt = self.build_prompt_for_sg_cot()

        messages = [
            {"role": "user", "content": full_prompt},
        ]

        # excute the module
        success = False
        patience = self.sg_patience
        count = 0
        while count < patience and not success:
            if self.sg_temperature < 0.1 and count > 0:
                _temperature = min(self.sg_temperature + 0.1, 1.0)
            else:
                _temperature = self.sg_temperature
            # solution = get_qwen_response(messages, self.api_key, self.sg_engine, _temperature, self.sg_max_tokens)
            solution = get_chat_response(messages, self.api_key, self.sg_engine, _temperature, self.sg_max_tokens)
            # print(f"Solution: {solution}")
            if extract_answer_choice(solution, self.cache["example"]["choices"]) is not None:
                success = True
            count += 1

        # Match run.py's (input, output) module interface without changing state.
        return test_prompt, solution

    def solution_generator(self):
        # get the module input
        if self.model == "chameleon":
            test_prompt, full_prompt = self.build_prompt_for_sg_chameleon()
        else:
            test_prompt, full_prompt = self.build_prompt_for_sg_cot()

        messages = [
            {"role": "user", "content": full_prompt},
        ]

        # excute the module
        success = False
        patience = self.sg_patience
        count = 0
        while count < patience and not success:
            if self.sg_temperature < 0.1 and count > 0:
                _temperature = min(self.sg_temperature + 0.1, 1.0)
            else:
                _temperature = self.sg_temperature
            # solution = get_qwen_response(messages, self.api_key, self.sg_engine, _temperature, self.sg_max_tokens)
            image_file = self.cache["example"].get("image_file", "")
            if image_file:
                solution = get_vision_response(
                    image_file,
                    full_prompt,
                    model=self.sg_engine,
                    temperature=_temperature,
                    max_tokens=self.sg_max_tokens,
                )
            else:
                solution = get_chat_response(messages, self.api_key, self.sg_engine, _temperature, self.sg_max_tokens)
            # print(f"Solution: {solution}")
            if extract_answer_choice(solution, self.cache["example"]["choices"]) is not None:
                success = True
            count += 1

        # update the cache
        self.cache["solution"] = solution
        self.cache["solution_generator:input"] = test_prompt
        self.cache["solution_generator:output"] = solution
        return test_prompt, solution

    def answer_generator(self):
        # get the module input
        output = self.cache["solution"]
        options = self.cache["example"]["choices"]
        # Accept the same explicit choice letter with Markdown or parentheses.
        prediction = extract_answer_choice(output, options)
        if prediction is None:
            prediction = "Not able to answer the question"

        # update the cache
        self.cache["prediction"] = prediction
        self.cache["answer_generator:input"] = output
        self.cache["answer_generator:output"] = prediction
        return output, prediction
