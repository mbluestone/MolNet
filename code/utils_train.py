# SideEffectsMayInclude
# Max Bluestone

# Training a graph/text model

import config
from utils_data import *
from models import *

import time

from os.path import join as path_join
from os.path import dirname

from sklearn.metrics import hamming_loss, recall_score, precision_score, f1_score, confusion_matrix, roc_auc_score


########################## MODEL CREATION ###########################

def create_model(model_params_dict,
                 device: torch.device) -> FullModel:
    """
    Instantiate the model.
    Args:
        num_graph_layers: Number of layers to use in the model.
        num_classes: Number of classes in the dataset.
        pretrain_load_path: Use pretrained weights.
    Returns:
        The instantiated model with the requested parameters.
    """

    # make sure a correct model type is requested
    possible_models = ['graph', 'text', 'combo']
    assert model_params_dict['model_type'].lower() in possible_models, f"Model type must be one of {possible_models} not {model_params_dict['model_type']}"
        
    # create the model
    if not model_params_dict['pretrain_load_path']:
        model = FullModel(model_type=model_params_dict['model_type'], 
                          num_classes=model_params_dict['num_classes'], 
                          num_node_features=model_params_dict['num_node_features'], 
                          graph_layers_sizes=model_params_dict['graph_layers_sizes'],  
                          text_embed_dim=model_params_dict['text_embed_dim'], 
                          text_output_dim=model_params_dict['text_output_dim'], 
                          linear_layers_sizes=model_params_dict['linear_layers_sizes'], 
                          dropout_rate=model_params_dict['dropout_rate'],
                          vocab_size=model_params_dict['vocab_size'])
    

    # if loading a pretrained model from a state dict
    else:
        
        ckpt = torch.load(f=pretrain_load_path,map_location=device)
        model_params_dict = ckpt["model_params_dict"]    
        model = FullModel(model_type=model_params_dict['model_type'], 
                          num_classes=model_params_dict['num_classes'], 
                          num_node_features=model_params_dict['num_node_features'], 
                          graph_layers_sizes=model_params_dict['graph_layers_sizes'],  
                          text_embed_dim=model_params_dict['text_embed_dim'], 
                          text_output_dim=model_params_dict['text_output_dim'], 
                          linear_layers_sizes=model_params_dict['linear_layers_sizes'], 
                          dropout_rate=model_params_dict['dropout_rate'],
                          vocab_size=model_params_dict['vocab_size'])

        model.load_state_dict(state_dict=ckpt["model_state_dict"])
        
    # transfer model to cpu or gpu
    model = model.to(device=device)
        
    return model

################################## MODEL TRAINING ##################################

def get_parameters(config):
    
    model_training_params_dict = {param: getattr(config,param) for param in dir(config) if "__" not in param}
    
    for param, value in model_training_params_dict.items():
        print(f'{param}: {value}')
            
    return model_training_params_dict
    

def train_helper(model: torch.nn.Module,
                 device: torch.device,
                 optimizer,
                 scheduler,
                 labels: list, 
                 dataloaders: dict,
                 dataset_sizes: dict,
                 criterion: torch.nn.modules.loss, 
                 writer,
                 model_params_dict: dict):
    '''
    Helper function for training model
    
    Args:
        model: torch.nn.Module, 
        labels: list, 
        num_epochs: int, 
        dataloaders: dict,
        dataset_sizes: dict,
        criterion: , 
        log_file: str, 
        log_csv: str
    '''
    
    # start tracking time
    start = time.time()
    
    # loop through epochs
    for epoch in range(model_params_dict['num_epochs']):

        print(f'Epoch {epoch}:')
        
        current_lr = None
        for group in optimizer.param_groups:
            current_lr = group["lr"]
            
        print(f'Current LR: {current_lr:.5f}')
        
        # Training
        model.train()

        # initialize running loss and accuracy for the epoch
        train_running_loss = 0.0
        train_running_accuracy = 0.0
        train_running_precision = 0.0
        train_running_recall = 0.0
        train_running_f1 = 0.0
        train_running_roc_auc = 0.0
        
        all_train_labels = np.array([])
        all_train_predictions = np.array([])

        # loop through batched training data
        for inputs in dataloaders['train']:
            
            # send to device
            inputs.y = inputs.y.to(device)
            inputs.x = inputs.x.to(device)
            inputs.edge_index = inputs.edge_index.to(device)
            inputs.batch = inputs.batch.to(device)
            inputs.text = inputs.text.to(device)

            # 
            optimizer.zero_grad()
            with torch.set_grad_enabled(mode=True):
                
                # make predicitions
                out = model(inputs)
                
                # calculate loss
                train_loss = criterion(out, inputs.y)
                
                # pull out batch labels
                train_batch_labels = inputs.y.cpu().numpy()
                
                train_batch_probs = torch.sigmoid(out).detach().cpu().numpy()
                train_batch_predictions = (torch.sigmoid(out)>0.5).detach().cpu().numpy()
                #print(train_batch_probs)
                
                # backpropagate
                train_loss.backward()
                
                # step optimizer
                optimizer.step()
                
                # calculate performance metrics 
                train_acc = 1-hamming_loss(train_batch_labels,train_batch_predictions)
                train_precision = precision_score(train_batch_labels,train_batch_predictions,
                                                  average='micro',zero_division=0)
                train_recall = recall_score(train_batch_labels,train_batch_predictions,
                                            average='micro',zero_division=0)
                train_f1 = f1_score(train_batch_labels,train_batch_predictions,
                                    average='micro',zero_division=0)
                try:
                    train_roc_auc = roc_auc_score(train_batch_labels,train_batch_probs,
                                                  average='micro')
                except Exception as e:
                    print("Error computing training ROC AUC:",e)
                
            # update running metrics
            train_running_loss += train_loss.item() * inputs.y.size(0)
            train_running_accuracy += train_acc * inputs.y.size(0)
            train_running_precision += train_precision * inputs.y.size(0)
            train_running_recall += train_recall * inputs.y.size(0)
            train_running_f1 += train_f1 * inputs.y.size(0)
            train_running_roc_auc += train_roc_auc * inputs.y.size(0)
            
            if all_train_labels.size == 0:
                all_train_labels = train_batch_labels
                all_train_predictions = train_batch_predictions
            else:
                all_train_labels = np.vstack((all_train_labels,train_batch_labels))
                all_train_predictions = np.vstack((all_train_predictions,train_batch_predictions))

        # calculate training metrics for the epoch
        epoch_train_loss = np.round(train_running_loss/dataset_sizes['train'],
                                  decimals=4)
        epoch_train_acc = np.round(train_running_accuracy/dataset_sizes['train'],
                                  decimals=4)
        epoch_train_precision = np.round(train_running_precision/dataset_sizes['train'],
                                  decimals=4)
        epoch_train_recall = np.round(train_running_recall/dataset_sizes['train'],
                                  decimals=4)
        epoch_train_f1 = np.round(train_running_f1/dataset_sizes['train'],
                                  decimals=4)
        epoch_train_roc_auc = np.round(train_running_roc_auc/dataset_sizes['train'], decimals=4)

        print(f'Training:\n'
              f'Loss = {epoch_train_loss}, ' 
              f'Accuracy = {epoch_train_acc}, '
              f'Precision = {epoch_train_precision}, '
              f'Recall = {epoch_train_recall}, '
              f'F1 = {epoch_train_f1}, '
              f'ROC_AUC = {epoch_train_roc_auc}') 
    
                
        # Validation
        model.eval()

        # initialize running loss and accuracy for the epoch
        val_running_loss = 0.0
        val_running_accuracy = 0.0
        val_running_precision = 0.0
        val_running_recall = 0.0
        val_running_f1 = 0.0
        val_running_roc_auc = 0.0
        
        all_val_labels = np.array([])
        all_val_predictions = np.array([])

        # loop through batched validation data
        for inputs in dataloaders['val']:
            
            # send to device
            inputs.y = inputs.y.to(device)
            inputs.x = inputs.x.to(device)
            inputs.edge_index = inputs.edge_index.to(device)
            inputs.batch = inputs.batch.to(device)
            inputs.text = inputs.text.to(device)

            with torch.set_grad_enabled(mode=False):
                
                # make predicitions
                out = model(inputs)
                
                # calculate loss
                val_loss = criterion(out, inputs.y)
                
                # pull out batch labels
                val_batch_labels = inputs.y.cpu().numpy()
                
                val_batch_probs = torch.sigmoid(out).detach().cpu().numpy()
                val_batch_predictions = (torch.sigmoid(out)>0.5).detach().cpu().numpy()
                
                # calculate performance metrics
                val_acc = 1-hamming_loss(val_batch_labels,val_batch_predictions)
                val_precision = precision_score(val_batch_labels,val_batch_predictions,
                                                average='micro',zero_division=0)
                val_recall = recall_score(val_batch_labels,val_batch_predictions,
                                          average='micro',zero_division=0)
                val_f1 = f1_score(val_batch_labels,val_batch_predictions,
                                  average='micro',zero_division=0)
                try:
                    val_roc_auc = roc_auc_score(val_batch_labels,val_batch_probs,
                                                average='micro')
                except Exception as e:
                    print("Error computing validation ROC AUC:",e)

            # update running metrics
            val_running_loss += val_loss.item() * inputs.y.size(0)
            val_running_accuracy += val_acc * inputs.y.size(0)
            val_running_precision += val_precision * inputs.y.size(0)
            val_running_recall += val_recall * inputs.y.size(0)
            val_running_f1 += val_f1 * inputs.y.size(0)
            val_running_roc_auc += val_roc_auc * inputs.y.size(0)
            
            if all_val_labels.size == 0:
                all_val_labels = val_batch_labels
                all_val_predictions = val_batch_predictions
            else:
                all_val_labels = np.vstack((all_val_labels,val_batch_labels))
                all_val_predictions = np.vstack((all_val_predictions,val_batch_predictions))

        # calculate validation metrics for the epoch
        epoch_val_loss = np.round(val_running_loss/dataset_sizes['val'],
                                  decimals=4)
        epoch_val_acc = np.round(val_running_accuracy/dataset_sizes['val'],
                                  decimals=4)
        epoch_val_precision = np.round(val_running_precision/dataset_sizes['val'],
                                  decimals=4)
        epoch_val_recall = np.round(val_running_recall/dataset_sizes['val'],
                                  decimals=4)
        epoch_val_f1 = np.round(val_running_f1/dataset_sizes['val'],
                                  decimals=4)
        epoch_val_roc_auc = np.round(val_running_roc_auc/dataset_sizes['val'],
                                     decimals=4)
        
        # empty cuda cache
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        # step scheduler
        scheduler.step()

        print(f'Validation:\n'
              f'Loss = {epoch_val_loss}, ' 
              f'Accuracy = {epoch_val_acc}, '
              f'Precision = {epoch_val_precision}, '
              f'Recall = {epoch_val_recall}, '
              f'F1 = {epoch_val_f1}, '
              f'ROC_AUC = {epoch_val_roc_auc}\n') 
        
        # log metrics in log csv
        writer.writerow('{},{:4f},{:4f},{:4f},{:4f},{:4f},{:4f},{:4f},{:4f}\n'.format(
            str(epoch), epoch_train_loss, epoch_train_acc, epoch_train_f1, epoch_train_roc_auc,
            epoch_val_loss, epoch_val_acc, epoch_val_f1, epoch_train_roc_auc).split(','))
    
    # save model
    torch.save(obj={"model_state_dict": model.state_dict(), 
                    "optimizer_state_dict": optimizer.state_dict(), 
                    "scheduler_state_dict": scheduler.state_dict(),
                    "model_params_dict": model_params_dict}, 
               f="trained_models/{}_{}_model.pt".format(model_params_dict['model_name'],model_params_dict['model_type']))
    
    # Print training information at the end.
    print(f"\nTraining complete in "
          f"{(time.time() - start) // 60:.2f} minutes")


