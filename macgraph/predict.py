
import tensorflow as tf
import numpy as np
from collections import Counter
from colored import fg, bg, stylize
import math
import argparse
import yaml
import os.path

from .input.text_util import UNK_ID
from .estimator import get_estimator
from .input import *
from .const import EPSILON
from .args import get_git_hash

import logging
logger = logging.getLogger(__name__)

# Block annoying warnings
def hr(bold=False):
	if bold:
		print(stylize("--------------------------", fg("yellow")))
	else:
		print(stylize("---------------", fg("blue")))

DARK_GREY = 235
WHITE = 255

BG_BLACK = 232
BG_DARK_GREY = 237

ATTN_THRESHOLD = 0.25

np.set_printoptions(precision=3)


def color_text(text_array, levels, color_fg=True):
	out = []
	for l, s in zip(levels, text_array):
		l_n = max(0.0, min(1.0, l))
		if color_fg:
			color = fg(int(math.floor(DARK_GREY + l_n * (WHITE-DARK_GREY))))
		else:
			color = bg(int(math.floor(BG_BLACK + l_n * (BG_DARK_GREY-BG_BLACK))))
		out.append(stylize(s, color))
	return out

def color_vector(vec, show_numbers=True):
	v_max = np.amax(vec)
	v_min = np.amin(vec)
	delta = np.abs(v_max - v_min)
	norm = (vec - v_min) / np.maximum(delta, 0.00001)

	def format_element(n):
		if show_numbers:
			return str(np.around(n, 4))
		else:
			return "-" if n < -EPSILON else ("+" if n > EPSILON else "0")

	def to_color(row):
		return ' '.join(color_text([format_element(i) for i in row], (row-v_min) / np.maximum(delta, EPSILON)))
	
	return [to_color(row) for row in vec]

def pad_str(s, target=3):
	if len(s) < target:
		for i in range(target - len(s)):
			s += " "
	return s

def adj_pretty(mtx, kb_nodes_len, kb_nodes, vocab):
	output = ""

	for r_idx, row in enumerate(mtx):
		if r_idx < kb_nodes_len:
			
			r_id = kb_nodes[r_idx][0]
			r_name = vocab.inverse_lookup(r_id)
			output += pad_str(f"{r_name}: ",target=4)
			
			for c_idx, item in enumerate(row):
				if c_idx < kb_nodes_len:

					c_id = kb_nodes[c_idx][0]
					c_name = vocab.inverse_lookup(c_id)

					if item:
						output += pad_str(f"{c_name}")
					else:
						output += pad_str(" ")
			output += "\n"

	return output




def predict(args, cmd_args):
	estimator = get_estimator(args)

	# Info about the experiment, for the record
	tfr_size = sum(1 for _ in tf.python_io.tf_record_iterator(args["predict_input_path"]))
	logger.info(args)
	logger.info(f"Predicting on {tfr_size} input records")

	# Actually do some work
	predictions = estimator.predict(input_fn=gen_input_fn(args, "predict"))
	vocab = Vocab.load_from_args(args)


	def print_query(i, prefix, row):
		switch_attn = row[f"{prefix}_switch_attn"][i]
		print(f"{i}: {prefix}_switch: ", 
			' '.join(color_text(args["query_sources"], row[f"{prefix}_switch_attn"][i])))
		print(np.squeeze(switch_attn), f"Σ={sum(switch_attn)}")

		for idx, part_noun in enumerate(args["query_sources"]):
			if row[f"{prefix}_switch_attn"][i][idx] > ATTN_THRESHOLD:

				if part_noun == "step_const":
					print(f"{i}: {prefix}_step_const_signal: {row[f'{prefix}_step_const_signal']}")
					db = None
				if part_noun.startswith("token"):
					db = row["src"]
				elif part_noun == "memory":
					db = list(range(args["memory_width"]//args["input_width"]))
				elif part_noun.startswith("prev_output"):
					db = list(range(i+1))

				if db is not None:
					attn_sum = sum(row[f'{prefix}_{part_noun}_attn'][i])
					assert attn_sum > 0.99, f"Attention does not sum to 1.0 {prefix}_{part_noun}_attn"
					v = ' '.join(color_text(db, row[f"{prefix}_{part_noun}_attn"][i]))
					print(f"{i}: {prefix}_{part_noun}_attn: {v} Σ={attn_sum}")

	def print_row(row):
		if p["actual_label"] == p["predicted_label"]:
			emoji = "✅"
			answer_part = f"{stylize(row['predicted_label'], bg(22))}"
		else:
			emoji = "❌"
			answer_part = f"{stylize(row['predicted_label'], bg(1))}, expected {row['actual_label']}"

		iterations = len(row["question_word_attn"])

		print(emoji, " ", answer_part, " - ", ''.join(row['src']).replace('<space>', ' ').replace('<eos>', ''))

		if cmd_args["hide_details"]:
			return

		for i in range(iterations):

			# print("iter_id", row["iter_id"][i])

			if args["use_control_cell"]:
				for idx, control_head in enumerate(row["question_word_attn"][i]):
					attn_sum = sum(control_head)
					attn_text = ' '.join(color_text(row["src"], control_head))
					print(f"{i}: {attn_text}")
					# print(f"{i}: attn {list(zip(row['src'], np.squeeze(control_head)))} Σ={attn_sum}")
					# print(f"{i}: attn_raw", list(zip(row["src"], row["question_word_attn_raw"][i][idx])))

			if args["use_read_cell"]:

				for head_i in range(args["read_heads"]):

					read_switch_parts = []
					for idx0, noun in enumerate(args["kb_list"]):
						read_switch_parts.append(f"{noun}{head_i}")

					if args["use_read_previous_outputs"]:
						read_switch_parts.extend(["prev_output_content", "prev_output_index"])

					if len(args["kb_list"]) > 0:
						read_head_part = ' '.join(color_text(read_switch_parts, row[f"read{head_i}_head_attn"][i]))
						print(f"{i}: read{head_i}_head_attn: {read_head_part}")
				
				
					for idx0, noun in enumerate(args["kb_list"]):
						if row[f"read{head_i}_head_attn"][i][idx0] > ATTN_THRESHOLD:

							print_query(i, f"{noun}{head_i}", row)

							db = [vocab.prediction_value_to_string(kb_row) for kb_row in row[f"{noun}s"]]
							print(f"{i}: {noun}{head_i}_attn: ",', '.join(color_text(db, row[f"{noun}{head_i}_attn"][i])))

							for idx, attn in enumerate(row[f"{noun}{head_i}_attn"][i]):
								if attn > ATTN_THRESHOLD and idx < len(row[f"{noun}s"]):
									print(f"{i}: {noun}{head_i}_word_attn: ",', '.join(color_text(
										vocab.prediction_value_to_string(row[f"{noun}s"][idx], True),
										row[f"{noun}{head_i}_word_attn"][i],
										)
									))

					if args["use_read_previous_outputs"]:
						for idx_, noun in enumerate(["po_content", "po_index"]):
							idx = idx_ + len(args["kb_list"])
							if row[f"read{head_i}_head_attn"][i][idx] > ATTN_THRESHOLD:
								v = f"read{head_i}_{noun}_attn"
								db = list(range(args["max_decode_iterations"]))
								print(f"{i}: {v}: ",', '.join(color_text(db, row[v][i])))


					hr()


			if args["use_message_passing"]:

				for mp_head in ["mp_write", "mp_read0"]:

					# -- Print node query ---
					print_query(i, mp_head+"_query", row)

					# --- Print node attn ---
					db = [vocab.prediction_value_to_string(kb_row[0:1]) for kb_row in row["kb_nodes"]]
					# db = db[0:row["kb_nodes_len"]]
					attn_sum = sum(row[mp_head+"_attn"][i])
					print(f"{i}: {mp_head}_attn: ",', '.join(color_text(db, row[mp_head+"_attn"][i])))
					# print(f"{i}: {tap}: ", list(zip(db, np.squeeze(row[tap][i]))), f"Σ={attn_sum}")

					for tap in ["query", "signal"]:
						print(f"{i}: {mp_head}_{tap}:  {row[f'{mp_head}_{tap}'][i]}")

				mp_state = color_vector(row['mp_node_state'][i][0:row['kb_nodes_len']])
				node_ids = [' node ' + pad_str(vocab.prediction_value_to_string(row[0])) for row in row['kb_nodes']]
				s = [': '.join(i) for i in zip(node_ids, mp_state)]
				mp_state_str = '\n'.join(s)
				print(f"{i}: mp_node_state:")
				print(mp_state_str)


			hr()


		if args["use_message_passing"]:
			print("Adjacency:\n",
				adj_pretty(row["kb_adjacency"], row["kb_nodes_len"], row["kb_nodes"], vocab))

		hr(bold=True)

	def decode_row(row):
		for i in ["type_string", "actual_label", "predicted_label", "src"]:
			row[i] = vocab.prediction_value_to_string(row[i], True)

	stats = Counter()
	output_classes = Counter()
	predicted_classes = Counter()
	confusion = Counter()

	for count, p in enumerate(predictions):
		if count >= cmd_args["n"]:
			break

		decode_row(p)
		if cmd_args["filter_type_prefix"] is None or p["type_string"].startswith(cmd_args["filter_type_prefix"]):
			if cmd_args["filter_output_class"] is None or p["predicted_label"] == cmd_args["filter_output_class"]:
				if cmd_args["filter_expected_class"] is None or p["actual_label"] == cmd_args["filter_expected_class"]:
					
					output_classes[p["actual_label"]] += 1
					predicted_classes[p["predicted_label"]] += 1

					correct = p["actual_label"] == p["predicted_label"]

					if cmd_args["failed_only"] and not correct:
						print_row(p)
					elif cmd_args["correct_only"] and correct:
						print_row(p)
					elif not cmd_args["failed_only"] and not cmd_args["correct_only"]:
						print_row(p)


if __name__ == "__main__":

	# --------------------------------------------------------------------------
	# Arguments
	# --------------------------------------------------------------------------
	parser = argparse.ArgumentParser()
	parser.add_argument("--n",type=int,default=20)
	parser.add_argument("--filter-type-prefix",type=str,default=None)
	parser.add_argument("--filter-output-class",type=str,default=None)
	parser.add_argument("--filter-expected-class",type=str,default=None)
	parser.add_argument("--model-dir",type=str,default=None)
	parser.add_argument("--model-dir-prefix",type=str,default="output/model")
	parser.add_argument('--dataset',type=str, default="default", help="Name of dataset")
	parser.add_argument("--model-version",type=str,default=get_git_hash())

	parser.add_argument("--correct-only",action='store_true')
	parser.add_argument("--failed-only",action='store_true')
	parser.add_argument("--hide-details",action='store_true')

	cmd_args = vars(parser.parse_args())

	if cmd_args["model_dir"] is None:
		cmd_args["model_dir"] = os.path.join(cmd_args["model_dir_prefix"], cmd_args["dataset"], cmd_args["model_version"])

	with tf.gfile.GFile(os.path.join(cmd_args["model_dir"], "config.yaml"), "r") as file:
		frozen_args = yaml.load(file)

	# If the directory got renamed, the model_dir might be out of sync, convenience hack
	frozen_args["model_dir"] = cmd_args["model_dir"]



	# --------------------------------------------------------------------------
	# Logging
	# --------------------------------------------------------------------------
	
	logging.basicConfig()
	tf.logging.set_verbosity("WARN")
	logger.setLevel("WARN")
	logging.getLogger("mac-graph").setLevel("WARN")

	

	# --------------------------------------------------------------------------
	# Lessssss do it!
	# --------------------------------------------------------------------------
	
	predict(frozen_args, cmd_args)



