import tensorflow as tf

from utils import PRF, print_metrics
from sklearn.model_selection import StratifiedKFold, train_test_split
from keras.models import Model
from keras.layers import *
from keras.optimizers import get
from preprocess.csv_reader import CsvReader
from preprocess.example_reader import ExampleReader
from keras.backend import tensorflow_backend as KTF

config = tf.ConfigProto()
config.gpu_options.allow_growth = True  # 不全部占满显存, 按需分配
sess = tf.Session(config=config)

KTF.set_session(sess)


class BaseModel:

    def __init__(self, args):

        # used dirs
        self.save_dir = args.save_dir
        self.word_embedding_dir = args.word_emb_dir
        self.char_embedding_dir = args.char_emb_dir

        # some basic parameters of the model
        self.model = None
        self.word_max_len = 43
        self.char_max_len = 57
        self.num_words = 9647
        self.num_chars = 2307
        self.args = args

        # pre-trained embeddings and their parameters.
        self.embedding_matrix = None
        self.char_embedding_matrix = None
        self.embedding_trainable = False
        self.EMBEDDING_DIM = 300

        self.train_word_inputs1, self.train_word_inputs2, self.train_label = None, None, None
        self.test_word_inputs1, self.test_word_inputs2 = None, None
        self.train_char_inputs1, self.train_char_inputs2 = None, None
        self.test_char_inputs1, self.test_char_inputs2 = None, None
        self.load_data()

        self.Q1, self.Q2, self.Q1_char, self.Q2_char = [None]*4
        self.Q1_emb, self.Q2_emb, self.Q1_char_emb, self.Q2_char_emb = [None]*4
        self.output = None  # (B, 2)

    def build_model(self):
        raise NotImplementedError

    def compile_model(self):

        self.Q1, self.Q2, self.Q1_char, self.Q2_char = self.make_input()
        self.Q1_emb, self.Q2_emb, self.Q1_char_emb, self.Q2_char_emb = self.embedded()
        self.output = self.build_model()

        if self.args.need_word_level:
            inputs = [self.Q1, self.Q2]
        else:
            inputs = []
        if self.args.need_char_level:
            inputs += [self.Q1_char, self.Q2_char]

        self.model = Model(inputs=inputs, outputs=self.output)
        optimizer = get({'class_name': self.args.optimizer, 'config': {'lr': self.args.lr}})
        self.model.compile(optimizer=optimizer, loss=self.args.loss, metrics=['acc'])
        self.model.summary()

    def one_train(self, epochs, batch_size,
                  train_data, train_label,
                  dev_data, dev_label):
        self.compile_model()
        for e in range(epochs):
            history = self.model.fit(train_data, train_label, batch_size=batch_size, verbose=1,
                                     validation_data=(dev_data, dev_label))
            dev_out = self.model.predict(dev_data, batch_size=2 * batch_size, verbose=1)
            metrics = PRF(dev_label, (dev_out > 0.5).astype('int32').reshape([-1]))
            metrics['epoch'] = e + 1
            metrics['val_loss'] = history.history['val_loss']
            print_metrics(metrics, metrics_type=self.__class__.__name__ + self.args.selfname,
                          save_dir=self.args.log_dir)

    def train_model(self, epochs, batch_size, kfold_num=0):
        inputs = [self.train_word_inputs1, self.train_word_inputs2,
                  self.train_char_inputs1, self.train_char_inputs2]
        if kfold_num > 1:
            kfold = StratifiedKFold(n_splits=kfold_num, shuffle=True)
            for train_index, dev_index in kfold.split(self.train_word_inputs1, self.train_label):
                train_data = [data[train_index] for data in inputs if data is not None]
                train_label = self.train_label[train_index]
                dev_data = [data[dev_index] for data in inputs if data is not None]
                dev_label = self.train_label[dev_index]

                self.one_train(epochs, batch_size,
                               train_data, train_label, dev_data, dev_label)

        else:
            inputs = [a for a in inputs if a is not None]
            print([a.shape for a in inputs])
            inputs.append(self.train_label)
            all_data = train_test_split(*inputs, test_size=0.2, random_state=1)
            train_data = [all_data[2*i] for i in range(len(inputs))]
            dev_data = [all_data[2*i + 1] for i in range(len(inputs))]

            self.one_train(epochs, batch_size,
                           train_data[:-1], train_data[-1],
                           dev_data[:-1], dev_data[-1])

    def predict(self):
        results = self.model.predict([self.test_word_inputs1, self.test_word_inputs2],
                                     batch_size=128, verbose=1)
        res = results.argmax(axis=1)

    def save_model(self, file=""):
        self.model.save_weights(self.save_dir + file)

    def load_data(self):
        r_dir = self.args.r_dir
        csv_reader = CsvReader(r_dir)
        print("read data from train.csv...")
        train_data, self.train_label = csv_reader.read_csv(name="train.csv", train=True)

        print("\nread data from test.csv...")
        test_data, _ = csv_reader.read_csv(name="test.csv", train=False)

        print("\nget word ids - index dic...")
        embedding_file = "word_embedding.txt"
        new_embedding_file = self.args.word_emb_dir
        word_id_index, word_unk = csv_reader.get_ids_from_embeddings(embedding_file, new_embedding_file)  # 9647

        print("\nget char ids - index dic...")
        embedding_file = "char_embedding.txt"
        new_embedding_file = self.args.char_emb_dir
        char_id_index, char_unk = csv_reader.get_ids_from_embeddings(embedding_file, new_embedding_file)  # 2307

        print("\nread question and convert the word id and char id to index using word/char ids - index dic...")
        id_question_words, id_question_chars = csv_reader.read_questions(name="question_id.csv",
                                                                         word_id_index=word_id_index,
                                                                         char_id_index=char_id_index,
                                                                         word_unk=word_unk,
                                                                         char_unk=char_unk)

        er = ExampleReader(r_dir)
        self.embedding_matrix = er.get_embedding_matrix(self.word_embedding_dir)
        if self.args.need_word_level:
            self.train_word_inputs1, self.train_word_inputs2 = er.question_pairs2question_inputs(inputs=train_data, id_questions=id_question_words, max_len=self.word_max_len)
            self.test_word_inputs1, self.test_word_inputs2 = er.question_pairs2question_inputs(inputs=test_data, id_questions=id_question_words, max_len=self.word_max_len)

        if self.args.need_char_level:
            self.char_embedding_matrix = er.get_embedding_matrix(self.char_embedding_dir)
            self.train_char_inputs1, self.train_char_inputs2 = er.question_pairs2question_inputs(inputs=train_data, id_questions=id_question_chars, max_len=self.char_max_len)
            self.test_char_inputs1, self.test_char_inputs2 = er.question_pairs2question_inputs(inputs=test_data, id_questions=id_question_chars, max_len=self.char_max_len)

    def read_model(self, file=""):
        self.compile_model()
        self.model.load_weights(self.save_dir + file)

    def make_input(self):
        # you can override this function depending on whether to use char level clues.
        if self.args.need_word_level:
            Q1 = Input(shape=[self.word_max_len], dtype='int32')
            Q2 = Input(shape=[self.word_max_len], dtype='int32')
            inputs = [Q1, Q2]
        else:
            inputs = [None, None]

        if self.args.need_char_level:
            Q1_char = Input(shape=[self.char_max_len], dtype='int32')
            Q2_char = Input(shape=[self.char_max_len], dtype='int32')
            inputs += [Q1_char, Q2_char]
        else:
            inputs += [None, None]
        return inputs

    def embedded(self):
        if self.args.need_word_level:
            shape = self.embedding_matrix.shape
            word_embedding = Embedding(shape[0], shape[1], mask_zero=True,
                                       weights=[self.embedding_matrix], trainable=self.args.word_trainable)
            Q1_emb = word_embedding(self.Q1)
            Q2_emb = word_embedding(self.Q2)
            embedded = [Q1_emb, Q2_emb]
        else:
            embedded = [None, None]

        if self.args.need_char_level:
            shape = self.char_embedding_matrix.shape
            char_embedding = Embedding(*shape, mask_zero=True,
                                       weights=[self.char_embedding_matrix], trainable=self.args.char_trainable)
            Q1_char_emb = char_embedding(self.Q1_char)
            Q2_char_emb = char_embedding(self.Q2_char)
            embedded += [Q1_char_emb, Q2_char_emb]
        else:
            embedded += [None, None]

        return embedded


if __name__ == '__main__':
    args = {'need_char_level': True}
    BaseModel(args).train_model(1, 10, 0)


