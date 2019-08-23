from http.server import (
	ThreadingHTTPServer as Server,
	BaseHTTPRequestHandler as BaseHandler
)
from pathtool import path_parse
import os.path
import json
import threading
from itertools import count, product
from chessfill import (
	ENV, DEFAULT_PIECES, PIECES, BINARY_TO_PIECE,
	Board,
	victory_chance, bitshift_amount
)
from urllib.parse import urlparse, parse_qsl
from fractions import Fraction as frac
import struct
fromhex = bytes.fromhex

import chessfill

CSS = """
body, h1, h2, h3 {
	min-width:800px;
	max-width:800px;
	text-align:center;
	margin:0 auto;
}
table {
	min-width:300px;
	max-width:300px;
	text-align:center;
	margin:0 auto;
}
}"""

HTML_START = """
<!DOCTYPE html>
<head>
<title>{title}</title>
<style>
{css}
</style>
</head>
<html>
<body>
"""
HTML_END = """
</body>
</html>
"""

BASE_HTML = HTML_START + "{body}" + HTML_END

PIECE_NAMES = ["Pawn", "Tower", "Knight", "Bishop", "Queen", "King"]

def load_solution(key):
	with ENV.begin() as txn:
		chance = txn.get(key+b"_chance")
		solution = txn.get(key+b"_moves")

	if chance is not None:
		chance = frac(*struct.unpack(">2Q", chance))

	if solution is not None:
		solution = json.loads(solution.decode())
		solution = dict(
			(BINARY_TO_PIECE[int(key)], value)
			for key, value
			in solution.items()
		)

	return chance, solution

def load_meta():
	with ENV.begin() as txn:
		stat = txn.stat()
	return None if stat is None else json.dumps(stat)

def ppf(f):
	return f"{f} ({100*float(f)}%)"

def iterate_keys(skip, keep):
	found = {}
	with ENV.begin() as txn:
		for key, _ in txn.cursor():
			if key.endswith(b"_moves"):
				continue
			if skip > 0:
				skip -= 1
				continue
			if keep <= 0:
				break

			keep -= 1

			yield key[:-7]

def handle_stat(wfile, path, query, qs):
	wfile.write(load_meta().encode())

def handle_keys(wfile, path, query, qs):
	skip = 0
	keep = 500
	try:
		skip = int(query["skip"])
	except Exception as e:
		pass

	try:
		keep = int(query["keep"])
	except Exception as e:
		pass

	keep = min(10000, keep)

	wfile.write(HTML_START.format(title="Keys", css=CSS).encode())

	for key in iterate_keys(skip, keep):
		key = key.hex()
		wfile.write(f'<a href="/result?key={key}">{key}</a>\n'.encode())

	wfile.write(HTML_END.encode())

def handle_result(wfile, path, query, qs):
	if "key" not in query:
		o = {'chance':None, 'solution':None}
	else:
		chance, solution = load_solution(fromhex(query["key"]))
		o = {
			'chance':float(chance) if chance is not None else None,
			'solution':solution
		}
	wfile.write(json.dumps(o).encode())

def create_board(wfile, qs, path, query):
	if "board" not in query:
		handle_blank(wfile, qs, path, query)
		return

	if "x" not in query or "y" not in query:
		wfile.write(b"Invalid position (missing)")
		return

	try:
		x = int(query["x"])
		y = int(query["y"])
	except:
		wfile.write(b"Invalid position (failed to parse)")
		return

	if x < 0 or x >= 4 or y < 0 or y >= 4:
		wfile.write(b"Invalid position (out of range)")
		return

	board_array = query["board"].split(" ")

	if len(board_array) < 16:
		wfile.write(b"Invalid board (not enough values)")
		return
	if len(board_array) > 16:
		wfile.write(b"Invalid board (too many values)")
		return

	selected_pos = 15-(x*4+y)
	selected_piece = None
	pieces = [piece[3] for piece in PIECES]
	for z, piece in enumerate(board_array):
		try:
			piece = int(piece)
		except:
			wfile.write(b"Invalid board (failed to parse int)")
			return

		if piece < 0 or piece > len(PIECES):
			wfile.write(b"Invalid board (out of range)")
			return

		if piece==0:
			continue

		if pieces[piece-1] <= 0:
			wfile.write(b"Invalid board (too many of single piece)")
			return
			
		if z == selected_pos:
			if piece != 0:
				selected_piece = piece-1
		else:
			pieces[piece-1] -= 1


	remaining_pieces = 0
	for piece, count in zip(PIECES, pieces):
		shift = piece[2]
		for i in range(count):
			remaining_pieces |= 1<<(shift+i)

	board_map = 0
	for z, piece in enumerate(board_array):
		board_map <<= 1
		if int(piece) != 0 and z!=selected_pos:
			board_map += 1

	board = Board(
		4, 4,
		x, y,
		remaining_pieces,
		board_map,
	)

	return board, board_array, pieces, selected_piece

def visualize(qs, board, board_array, pieces):
	finished = "0" not in board_array

	pieces = [
		(PIECE_NAMES[piece], count)
		for piece, count
		in enumerate(pieces)
	]

	board_array = [
		[
			PIECE_NAMES[int(i)-1] if int(i) != 0 else "Empty"
			for i in board_array[j::4][::-1]
		]
		for j in range(4)
	]

	selected_str = board_array[3-board.prev_y][board.prev_x]
	selected_str = f"<font color='green'>{selected_str}</color>"
	board_array[3-board.prev_y][board.prev_x] = selected_str

	chance, positions = load_solution(board.hash())
	victory_chance = "Possibility of winning not yet computed<br>"
	if chance is not None:
		victory_chance = f"Possibility of winning: {ppf(chance)}<br>"
	elif finished:
		victory_chance = "<h3>Victory!</h3><br>"

	return "\n"+victory_chance+"<br>\n".join(
			f"{piece} x {count}<br>"
			for piece, count in pieces
		) + "\n<table>" + "\n".join(
			"<tr>\n"+
			"\n".join(f"<th>{board_piece}</th>" for board_piece in board_row) +
			"\n</tr>"
			for board_row in board_array
		) + f"</table>\nLast position was {board.prev_x},{board.prev_y}<br>\n"


def handle_blank(wfile, qs, path, query):
	link = '<th><a href="/board?board='+"+".join("0"*16)+'&x={0}&y={1}">[{0},{1}]</a></th>'

	table_str = "<table>" + "\n".join(
		"<tr>\n"+
		"\n".join(link.format(x, y) for x in range(4)) +
		"\n</tr>"
		for y in range(3, -1, -1)
	) + "</table>"


	wfile.write(BASE_HTML.format(
		title="Chessfill Solver",
		css=CSS,
		body=f"""
<h1>Choose your starting position to get started</h1><br>
<form action="/board">
	<p>Enter starting position</p>
	<input type="hidden" name="board" value="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"/>
	<select name="x">
		<option value="0">0</option>
		<option value="1">1</option>
		<option value="2">2</option>
		<option value="3">3</option>
	</select>
	<select name="y">
		<option value="0">0</option>
		<option value="1">1</option>
		<option value="2">2</option>
		<option value="3">3</option>
	</select>
	<button type="submit">Submit</button>
	<form>
</form>
{table_str}<br>
<br>
<br>
<h3>No idea what this is?</h3>
This is a website designed to give me the optimal solution to <a href="https://games.increpare.com/schachdeckel/">Chessfill</a> by <a href="https://www.increpare.com/">Stephen Lavelle</a><br><br>I got a 50USD trial for a server on digitalocean, so I had to figure out a way to spend them within the month, so I decided to just run a script that bruteforced it. The code for this is really messy, but can be found at <a href="https://github.com/ITR13/ChessfillBruteforcer">https://github.com/ITR13/ChessfillBruteforcer</a>if you want to see it.<br><br>Ended up having to restart the script 4 times since I noticed mistakes in my assumptions, lol.
		"""
	).encode())


def handle_visualize(wfile, path, query, qs):
	board_data = create_board(wfile, qs, path, query)
	if board_data is None:
		return
	board, board_array, pieces, _ = board_data

	key = board.hash().hex()
	wfile.write(BASE_HTML.format(
		title="Visualizer",
		css=CSS,
		body=visualize(qs, board, board_array, pieces) +
		f'\n<a href="/result?key={key}">{key}</a>\n'
	).encode())



def handle_select_piece(wfile, qs, board, board_array, pieces):
	chance, positions = load_solution(board.hash())

	selected_pos = 15-(board.prev_x*4+board.prev_y)
	new_arrays = [
		[
			piece if i != selected_pos else selected+1
			for i, piece in enumerate(board_array)
		]
		for selected in range(len(pieces))
	]
	
	s = "<br>\n".join([
		f'<a href="/board' +
		f'?x={board.prev_x}&y={board.prev_y}' +
		f'&board={"+".join(map(str, new_array))}">{PIECE_NAMES[piece]}</a>' +
		(
			'' if positions is None else 
			f" ({len(positions[piece])} legal positions)"
		)
		for piece, count, new_array
		in zip(count(), pieces, new_arrays)
		if count > 0
	])
	
	


	wfile.write(BASE_HTML.format(
	title="select piece",
	css=CSS,
	body = f"""
<h1>Select next piece</h1><br>
{s}<br>
<br>
<h2>Visualization</h2><br>
{visualize(qs, board, board_array, pieces)}
	"""
	).encode())

def handle_select_position(wfile, qs, piece, board, board_array, pieces):
	chance, positions = load_solution(board.hash())
	if positions == None:
		wfile.write(BASE_HTML.format(
			title="select position",
			css=CSS,
			body = "Missing legal positions<br>"+visualize(qs, board, board_array, pieces)
		).encode())
		return

	if piece not in positions:
		wfile.write(BASE_HTML.format(
			title="select position",
			css=CSS,
			body = "Invalid piece (should not happen)<br>"+visualize(qs, board, board_array, pieces)
		).encode())
		return

	qs += "&"
	qs = qs.replace(f"x={board.prev_x}&", "").replace(f"y={board.prev_y}&", "")

	choices = positions[piece]
	new_states = []
	for choice in choices:
		bag = PIECES[piece][0]&board.remaining_pieces
		bindex = bitshift_amount(bag)
		temp_board = board.place(bindex, choice['x'], choice['y'])

		new_positions = temp_board.state
		new_state = [
			board_piece if int(board_piece) != 0 else
			str(piece + 1) if new_positions&1<<15-i != 0 else
			"0"
			for i, board_piece
			in enumerate(board_array)
		]
		new_states.append((new_state, choice['x'], choice['y']))

	links = [
		f"/board?{qs}x={x}&y={y}"
		for new_state, x, y in new_states
	]

	s = "<br>\n".join(
		f"<a href='{l}'><b>x = {c['y']}, y = {c['x']}</b></a>" +
		f": Chance of Victory " +
		ppf(frac(c['numerator'], c['denominator']))
		for c, l in zip(choices, links)
	)

	wfile.write(BASE_HTML.format(
	title="select position",
	css=CSS,
	body = f"""
<h1>Select next position</h1><br>
<h3><b>Current Piece:</b> {PIECE_NAMES[piece]}</h3><br>
{s}<br>
<br>
<h2>Visualization</h2><br>
{visualize(qs, board, board_array, pieces)}
	"""
	).encode())

def handle_board(wfile, path, query, qs):
	board_data = create_board(wfile, qs, path, query)
	if board_data is None:
		return
	board, board_array, pieces, selected_piece = board_data

	if "0" not in board_array:
		wfile.write(BASE_HTML.format(
			title="Victory",
			css=CSS,
			body = visualize(qs, board, board_array, pieces)
		).encode())
		return

	if selected_piece is None:
		handle_select_piece(wfile, qs, board, board_array, pieces)
		return

	handle_select_position(
		wfile,
		qs,
		selected_piece,
		board,
		board_array,
		pieces
	)



HANDLERS = {
	"stat": handle_stat,
	"keys": handle_keys,
	"result": handle_result,
	"board": handle_board,
	"viz": handle_visualize,
	"": handle_blank,
}

class Handler(BaseHandler):
	def do_GET(self):
		parsed = urlparse(self.path)
		path = path_parse(parsed.path)
		if len(path) == 0:
			path = ['']

		if path[0] not in HANDLERS:
			self.send_response(404)
			self.end_headers()
			return
		self.send_response(200)
		self.end_headers()

		query = dict(parse_qsl(parsed.query))

		HANDLERS[path[0]](self.wfile, path, query, parsed.query)
		return

if __name__ == '__main__':
	server = Server(('', 80), Handler)
	print('Starting server, use <Ctrl-C> to stop')
	server.serve_forever()