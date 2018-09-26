import argparse
import sys

from tensorflow.examples.tutorials.mnist import input_data
import tensorflow as tf

from tensorflow.python.client import device_lib
print("*******Device Info Seen By TensorFlow*******")
print(device_lib.list_local_devices())
print("********************************************")

def create_nodelist(SLURM_JOB_NODELIST):
        nodes = re.findall(r'\d+', SLURM_JOB_NODELIST)
        nodelist = [i + '.pvt.bridges.psc.edu:2222' for i in nodes]
        return nodelist

def get_nodelist():
        node_count = int(os.environ['SLURM_JOB_NUM_NODES'])
        SLURM_JOB_NODELIST = os.environ['SLURM_JOB_NODELIST']
        print('node count=' + str(node_count))
        print('SLURM_JOB_NODELIST='+SLURM_JOB_NODELIST)
        if node_count >1:
                nodelist = create_nodelist(SLURM_JOB_NODELIST)
                print(nodelist)
        else:
                nodelist = [SLURM_JOB_NODELIST + '.pvt.bridges.psc.edu:2222']
        return nodelist

def create_cluster_dict(nodelist):
        d = {}
        ps_list = []
        worker_list = []
        for i in range(len(nodelist)):
                if i == 0:
                        ps_list.append(nodelist[i])
                else:
                        worker_list.append(nodelist[i])
        d['ps'] = ps_list
        d['worker'] = worker_list
        return d

FLAGS = None

def deepnn(x):
  x_image = tf.reshape(x, [-1, 28, 28, 1])

  W_conv1 = weight_variable([5, 5, 1, 32])
  b_conv1 = bias_variable([32])
  h_conv1 = tf.nn.relu(conv2d(x_image, W_conv1) + b_conv1)

  h_pool1 = max_pool_2x2(h_conv1)

  W_conv2 = weight_variable([5, 5, 32, 64])
  b_conv2 = bias_variable([64])
  h_conv2 = tf.nn.relu(conv2d(h_pool1, W_conv2) + b_conv2)

  h_pool2 = max_pool_2x2(h_conv2)

  W_fc1 = weight_variable([7 * 7 * 64, 1024])
  b_fc1 = bias_variable([1024])

  h_pool2_flat = tf.reshape(h_pool2, [-1, 7*7*64])
  h_fc1 = tf.nn.relu(tf.matmul(h_pool2_flat, W_fc1) + b_fc1)

  keep_prob = tf.placeholder(tf.float32)
  h_fc1_drop = tf.nn.dropout(h_fc1, keep_prob)

  W_fc2 = weight_variable([1024, 10])
  b_fc2 = bias_variable([10])

  y_conv = tf.matmul(h_fc1_drop, W_fc2) + b_fc2
  return y_conv, keep_prob

def conv2d(x, W):
  return tf.nn.conv2d(x, W, strides=[1, 1, 1, 1], padding='SAME')

def max_pool_2x2(x):
  return tf.nn.max_pool(x, ksize=[1, 2, 2, 1],
                        strides=[1, 2, 2, 1], padding='SAME')

def weight_variable(shape):
  initial = tf.truncated_normal(shape, stddev=0.1)
  return tf.Variable(initial)

def bias_variable(shape):
  initial = tf.constant(0.1, shape=shape)
  return tf.Variable(initial)

def create_queue(job_name, task_index, worker_hosts):
  with tf.device("/job:%s/task:%d" % (job_name, task_index)):
    return tf.FIFOQueue(len(worker_hosts), tf.int32, shared_name="queue_"+str(job_name)+"_"+str(task_index))

def main(_):
  ##ps_hosts = FLAGS.ps_hosts.split(",")
  ##worker_hosts = FLAGS.worker_hosts.split(",")
  nodelist = get_nodelist()
  print(nodelist)
  d = create_cluster_dict(nodelist)
  print("Cluster Spec :: ")
  print(d)
  ps_hosts = d['ps']
  worker_hosts = d['worker']
  # Create a cluster from the parameter server and worker hosts.
  cluster = tf.train.ClusterSpec(d)

  # Create and start a server for the local task.
  server = tf.train.Server(cluster,
                           job_name=FLAGS.job_name,
                           task_index=FLAGS.task_index)

  if FLAGS.job_name == "ps":

    # Control shutdown of parameter server in queue instead of server.join() function.
    queue = create_queue(FLAGS.job_name, FLAGS.task_index, worker_hosts)

    with tf.Session(server.target) as sess:
      for i in range(len(worker_hosts)):
        sess.run(queue.dequeue())

  elif FLAGS.job_name == "worker":

    # Assigns ops to the local worker by default.
    with tf.device(tf.train.replica_device_setter(
        worker_device="/job:worker/task:%d" % FLAGS.task_index,
        cluster=cluster)):

      # Import data
      mnist = input_data.read_data_sets(FLAGS.data_dir, one_hot=True)

      # Build Deep MNIST model...
      x = tf.placeholder(tf.float32, [None, 784])
      y_ = tf.placeholder(tf.float32, [None, 10])
      y_conv, keep_prob = deepnn(x)

      cross_entropy = tf.reduce_mean(
          tf.nn.softmax_cross_entropy_with_logits(labels=y_, logits=y_conv))

      global_step = tf.contrib.framework.get_or_create_global_step()

      train_step = tf.train.AdamOptimizer(1e-4).minimize(cross_entropy, global_step=global_step)
      correct_prediction = tf.equal(tf.argmax(y_conv, 1), tf.argmax(y_, 1))
      accuracy = tf.reduce_mean(tf.cast(correct_prediction, tf.float32))

    # Create queues for all servers participating in the cluster.
    queue = create_queue(FLAGS.job_name, FLAGS.task_index, worker_hosts)
    queues = []
    for i in range(len(ps_hosts)):
      queues.append(create_queue("ps", i, worker_hosts))
    for i in range(len(worker_hosts)):
      queues.append(create_queue("worker", i, worker_hosts))

    # The StopAtStepHook handles stopping after running given steps.
    hooks=[tf.train.StopAtStepHook(last_step=1000)]

    # The MonitoredTrainingSession takes care of session initialization,
    # restoring from a checkpoint, saving to a checkpoint, and closing when done
    # or an error occurs.
    with tf.train.MonitoredTrainingSession(master=server.target,
                                           is_chief=(FLAGS.task_index == 0),
                                           checkpoint_dir=FLAGS.log_dir,
                                           hooks=hooks) as mon_sess:
      i = 0
      while not mon_sess.should_stop():
        # Run a training step asynchronously.
        batch = mnist.train.next_batch(50)
        if i % 100 == 0:
          train_accuracy = mon_sess.run(accuracy, feed_dict={
              x: batch[0], y_: batch[1], keep_prob: 1.0})
          print('global_step %s, task:%d_step %d, training accuracy %g'
                % (tf.train.global_step(mon_sess, global_step), FLAGS.task_index, i, train_accuracy))
        mon_sess.run(train_step, feed_dict={x: batch[0], y_: batch[1], keep_prob: 0.5})
        i = i + 1

    # Notification of task completion and wait for task completion of other worker server.
    with tf.Session(server.target) as sess:
      for q in queues:
        sess.run(q.enqueue(1))
      for i in range(len(worker_hosts)):
        sess.run(queue.dequeue())

if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.register("type", "bool", lambda v: v.lower() == "true")
  # Flags for defining the tf.train.ClusterSpec
  parser.add_argument(
      "--ps_hosts",
      type=str,
      default="",
      help="Comma-separated list of hostname:port pairs"
  )
  parser.add_argument(
      "--worker_hosts",
      type=str,
      default="",
      help="Comma-separated list of hostname:port pairs"
  )
  parser.add_argument(
      "--job_name",
      type=str,
      default="",
      help="One of 'ps', 'worker'"
  )
  # Flags for defining the tf.train.Server
  parser.add_argument(
      "--task_index",
      type=int,
      default=0,
      help="Index of task within the job"
  )
  # Flags for specifying input/output directories
  parser.add_argument(
      "--data_dir",
      type=str,
      default="/tmp/mnist_data",
      help="Directory for storing input data")
  parser.add_argument(
      "--log_dir",
      type=str,
      default="/tmp/train_logs",
      help="Directory for train logs")
  FLAGS, unparsed = parser.parse_known_args()
  tf.app.run(main=main, argv=[sys.argv[0]] + unparsed)
