from math import nan
from itertools import count, product
import json
import struct
from fractions import Fraction as frac
from tqdm import tqdm
import lmdb

import signal
import sys

EXIT = False
def force_exit(sig, frame):
	global EXIT
	EXIT = True

def system_exitable(f):
	def exiter(*args, **kwargs):
		if EXIT:
			sys.exit(0)
		return f(*args, **kwargs)
	return exiter

if __name__ == "__main__":
	signal.signal(signal.SIGINT, force_exit)


refresh_dict = {}
def pbar(*args, **kwargs):
	refresh = False
	if "desc" in kwargs:
		depth = kwargs["desc"]
		if depth not in refresh_dict:
			refresh_dict[depth] = 1
			refresh = True
			kwargs["desc"] += "!"
		else:
			refresh_dict[depth] += 1
			kwargs["desc"] += "_"


	e = None
	bar = tqdm(*args, **kwargs)
	for values in bar:
		if refresh:
			bar.refresh()
		try:
			yield values
		except SystemExit as e:
			break

	if e is not None:
		raise e


DEBUG = False

W = 4
H = 4
DEFAULT_PIECES	= 0b1111111111111111
PAWNS			= 0b1111111100000000
ROOKS			= 0b0000000011000000
KNIGHTS			= 0b0000000000110000
BISHOPS			= 0b0000000000001100
QUEENS			= 0b0000000000000010
KINGS			= 0b0000000000000001


def iterate_ones(n):
	i = 0
	while n > 0:
		if n % 2 == 1:
			yield i
		i += 1
		n >>= 1

def count_ones(n):
	i = 0
	while n > 0:
		if n % 2 == 1:
			i += 1
		n >>= 1
	return i


def king_can_move(x, y, dx, dy):
	return abs(dx) <= 1 and abs(dy) <= 1

def knight_can_move(x, y, dx, dy):
	return abs(dx) + abs(dy) == 3 and dx != 0 and dy != 0

def bishop_can_move(x, y, dx, dy):
	return dx==dy or dx==-dy

def rook_can_move(x, y, dx, dy):
	return dx==0 or dy==0

def queen_can_move(x, y, dx, dy):
	return bishop_can_move(x, y, dx, dy)  or  rook_can_move(x, y, dx, dy)

def pawn_can_move(x, y, dx, dy):
	return (
		(y==3 and queen_can_move(x, y, dx, dy)) or
		(dx == 0 and ((y==1 and dy == 2) or  dy == 1))
	)

def bitshift_amount(bin):
	if bin == 0:
		return -1
	i = 0
	while bin%2 == 0:
		bin >>= 1
		i += 1
	return i

PIECES = [
	(PAWNS, pawn_can_move),
	(ROOKS, rook_can_move),
	(KNIGHTS, knight_can_move),
	(BISHOPS, bishop_can_move),
	(QUEENS, queen_can_move),
	(KINGS, king_can_move),
]

PIECES = [(i, j, bitshift_amount(i), count_ones(i)) for i, j in PIECES]


if __name__ == "__main__":
	print("Setting up binary to piece dict")

BINARY_TO_PIECE = dict(
	[
		(i, j)
		for _piece, j
		in zip(PIECES, count())
		for i in iterate_ones(_piece[0])
	]
)

@system_exitable
def get_weights(remaining_pieces):
	last_legal = [0]*len(PIECES)
	weight = [0]*len(PIECES)
	total = 0

	for i in iterate_ones(remaining_pieces):
		index = BINARY_TO_PIECE[i]
		last_legal[index] = i
		weight[index] += 1
		total += 1

	return [frac(w, total) for w in weight], last_legal

class Board:
	def __init__(
		self,
		w, h,
		prev_x, prev_y,
		remaining_pieces,
		state
	):
		self.w = w
		self.h = h
		self.prev_x = prev_x
		self.prev_y = prev_y
		self.remaining_pieces = remaining_pieces
		self.state = state

		if DEBUG:
			assert(0<=w<16)
			assert(0<=h<16)
			assert(0<=prev_x<16)
			assert(0<=prev_y<16)
			placed = count_ones(state)
			remaining = count_ones(remaining_pieces)
			assert(w*h-remaining == placed)
			assert(state&self.__pos(self.prev_x, self.prev_y)==0)

	def hash(self):
		return struct.pack(
			">2B",
			self.w+self.h*16,
			self.prev_x+self.prev_y*16,
		) + struct.pack(
			">2H",
			self.remaining_pieces, self.state
		)

	def __pos(self, x, y):
		return 1<<(x*self.h+y)

	def can_place(self, piece_index, x, y):
		if x == self.prev_x and y == self.prev_y:
			return False
		if x < 0  or  y < 0  or  x >= self.w  or  y >= self.h:
			return False
		if self.__pos(x, y) & self.state != 0:
			return False
		dx = x-self.prev_x
		dy = y-self.prev_y

		return PIECES[piece_index][1](self.prev_x, self.prev_y, dx, dy)

	@system_exitable
	def place(self, piece_binary_index, x, y):
		if self.remaining_pieces & 1<<piece_binary_index == 0:
			raise IndexError("Invalid piece")

		piece_index = BINARY_TO_PIECE[piece_binary_index]

		if not self.can_place(piece_index, x, y):
			return None

		remaining = self.remaining_pieces - (1<<piece_binary_index)
		state = self.state | self.__pos(self.prev_x, self.prev_y)

		if DEBUG:
			assert(count_ones(remaining) == count_ones(self.remaining_pieces)-1)
			assert(count_ones(state) == count_ones(self.state)+1)

		return Board(
			self.w, self.h,
			x, y,
			remaining,
			state
		)


ENV = lmdb.open("./chessfill_lmdb", map_size=1e11)

@system_exitable
def victory_chance(current_board, tqdm_nesting=0):
	if count_ones(current_board.state) == 15:
		return frac(1, 1)

	hash = current_board.hash()

	with ENV.begin() as txn:
		stored = txn.get(hash+b"_chance")

	if stored is not None:
		return frac(*struct.unpack(">2Q", stored))

	total_chance = frac(0,1)
	weights = zip(*get_weights(current_board.remaining_pieces))

	best_positions = {}

	if tqdm_nesting > 0:
		weights = pbar(
			weights,
			leave=False,
			total=len(PIECES),
			desc=f"Weights {tqdm_nesting-1}"
		)

	for weight, last_legal in weights:
		if weight == 0:
			continue

		boards = [
			(current_board.place(last_legal, x, y), x, y)
			for x in range(current_board.w)
			for y in range(current_board.h)
		]
		boards = [i for i in boards if i[0] is not None]

		if tqdm_nesting > 0:
			boards = pbar(boards, desc=f"Victory {tqdm_nesting-1}", leave=False)

		chances = [
			(victory_chance(board, tqdm_nesting-1), x, y)
			for board, x, y
			in boards
		]
		chances.sort(reverse=True)

		total_chance += 0 if len(chances) == 0 else chances[0][0]*weight
		chances = [
			{
				"numerator": chance._numerator,
				"denominator": chance.denominator,
				"x": x, "y": y
			}
			for chance, x, y in chances
		]
		best_positions[last_legal] = chances

	chance_packed = struct.pack(
		">2Q",
		total_chance._numerator,
		total_chance.denominator
	)
	moves_dumped = json.dumps(best_positions).encode()

	with ENV.begin(write=True) as txn:
		txn.put(hash+b"_chance", chance_packed)
		txn.put(hash+b"_moves", moves_dumped)

	return total_chance

if __name__ == "__main__":
	print("Calculating the chances of all positions")
	_starting_pos_chance = frac(1, W*H)

	_total_chance = frac(0, 1)

	for _x, _y in pbar(list(product(range(W), range(H))), leave=False):
		_state = 0
		_board = Board(W, H, _x, _y, DEFAULT_PIECES, _state)
		_chance = victory_chance(_board, int(sys.argv[1]))
		_total_chance += _chance * _starting_pos_chance

	print(f"The chance of success is {_total_chance}")